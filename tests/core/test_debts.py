from datetime import date

import pytest

from tuppence.core.db import Database
from tuppence.core.debts import DebtIn, DebtService
from tuppence.core.errors import InputError
from tuppence.core.household import HouseholdService, PersonIn
from tuppence.core.migrate import migrate
from tuppence.core.records import VersionConflict


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    hh = HouseholdService(db)
    a = hh.create_person(PersonIn(display_name="Alex Example", role="adult"))
    return DebtService(db, hh, today=lambda: date(2030, 1, 15)), hh, a


def _debt(kind="personal_loan", **kw):
    base = {"kind": kind, "lender": "Bank", "balance": "£1,200.50"}
    return DebtIn(**{**base, **kw})


def test_basic_and_default_balance_date(env):
    svc, _, a = env
    d = svc.create(_debt(person_id=a.id, apr=7.9, monthly_payment="100"))
    assert d.balance == "1200.50" and d.monthly_payment == "100.00" and d.apr == 7.9
    assert d.balance_date == date(2030, 1, 15)
    assert svc.total_balance_pence() == 120050
    explicit = svc.create(_debt(balance_date=date(2026, 1, 2)))
    assert explicit.balance_date == date(2026, 1, 2)
    with pytest.raises(InputError):
        svc.create(_debt(person_id="p_nobody"))


def test_student_loan_plan_rules(env):
    svc, *_ = env
    with pytest.raises(InputError, match="plan"):
        svc.create(_debt("student_loan"))
    with pytest.raises(InputError, match="only applies"):
        svc.create(_debt(student_loan_plan="plan2"))
    assert svc.create(_debt("student_loan", student_loan_plan="plan2")).student_loan_plan == "plan2"


def test_car_finance_redress_window(env):
    svc, *_ = env

    def pcp(start, broker=True):
        return svc.create(
            _debt(
                "car_finance_pcp",
                details={
                    "agreement_start": start,
                    "via_broker": broker,
                    "balloon": "£6,000",
                    "total_payable": "15000.99",
                    "annual_mileage": 8000,
                },
            )
        )

    d = pcp("2019-03-01")
    assert d.car_finance_redress_window is True
    assert d.details["balloon"] == "6000.00" and d.details["total_payable"] == "15000.99"
    with svc.db.connection() as conn:
        assert "balloon_pence" in conn.execute("SELECT details FROM debt").fetchone()[0]
    assert pcp("2025-01-01").car_finance_redress_window is False
    assert pcp("2019-03-01", broker=False).car_finance_redress_window is False
    assert pcp("2007-04-06").car_finance_redress_window is True
    assert pcp("2024-11-01").car_finance_redress_window is True
    assert pcp("2024-11-02").car_finance_redress_window is False
    assert pcp("2007-04-05").car_finance_redress_window is False


def test_hp_details_and_unknown_keys(env):
    svc, *_ = env
    assert svc.create(_debt("car_finance_hp", details={"via_broker": True})).details == {
        "via_broker": True
    }
    with pytest.raises(InputError, match="balloon"):
        svc.create(_debt("car_finance_hp", details={"balloon": "100"}))
    with pytest.raises(InputError, match="colour"):
        svc.create(_debt(details={"colour": "red"}))
    with pytest.raises(InputError):
        svc.create(_debt("car_finance_pcp", details={"balloon": "lots"}))
    with pytest.raises(InputError, match="agreement_start"):
        svc.create(_debt("car_finance_pcp", details={"agreement_start": "soon"}))


def test_mortgage_fixed_until(env):
    svc, *_ = env
    d = svc.create(_debt("mortgage", details={"fixed_until": "2028-05-01", "rate_type": "fixed"}))
    assert d.details == {"fixed_until": "2028-05-01", "rate_type": "fixed"}
    with pytest.raises(InputError, match="rate_type"):
        svc.create(_debt("mortgage", details={"rate_type": "magic"}))


def test_informal_direction_and_total(env):
    svc, *_ = env
    with pytest.raises(InputError, match="direction"):
        svc.create(_debt("informal", lender="Brother"))
    svc.create(_debt("informal", lender="Brother", balance="500", details={"direction": "i_owe"}))
    svc.create(_debt("informal", lender="Pal", balance="300", details={"direction": "owed_to_me"}))
    assert svc.total_balance_pence() == 50000


def test_apr_limits(env):
    svc, *_ = env
    with pytest.raises(ValueError):
        _debt(apr=101)
    with pytest.raises(ValueError):
        _debt(apr=5.123)


def test_settle_hides_and_blocks_edits(env):
    svc, *_ = env
    d = svc.create(_debt())
    s = svc.settle(d.id, d.version)
    assert s.status == "settled" and svc.list() == [] and len(svc.list(include_settled=True)) == 1
    assert svc.total_balance_pence() == 0
    with pytest.raises(InputError, match="already settled"):
        svc.settle(d.id, s.version)
    with pytest.raises(InputError):
        svc.update(d.id, {"lender": "X"}, s.version)
    r = svc.reopen(d.id, s.version)
    assert r.status == "active" and len(svc.list()) == 1
    with pytest.raises(InputError, match="already open"):
        svc.reopen(d.id, r.version)


def test_details_merge_on_edit(env):
    svc, *_ = env
    d = svc.create(
        _debt(
            "car_finance_pcp",
            details={"agreement_start": "2019-03-01", "balloon": "5000", "annual_mileage": 8000},
        )
    )
    assert d.car_finance_redress_window is False
    u = svc.update(d.id, {"details": {"via_broker": True}}, d.version)
    assert u.details["agreement_start"] == "2019-03-01" and u.details["balloon"] == "5000.00"
    assert u.car_finance_redress_window is True
    u2 = svc.update(d.id, {"details": {"balloon": None, "via_broker": False}}, u.version)
    assert "balloon" not in u2.details and u2.car_finance_redress_window is False
    # kind change drops keys that don't apply, and says so
    m = svc.update(d.id, {"kind": "car_finance_hp"}, u2.version)
    assert m.details_removed == ["annual_mileage"] and "annual_mileage" not in m.details
    with pytest.raises(InputError, match="balloon"):
        svc.update(d.id, {"kind": "mortgage", "details": {"balloon": "1"}}, m.version)


def test_update_only_validates_changes(env):
    svc, hh, a = env
    d = svc.create(_debt(person_id=a.id))
    hh.retire_person(a.id, a.version)
    u = svc.update(d.id, {"lender": "Other bank"}, d.version)
    assert u.lender == "Other bank" and u.version == 2
    with pytest.raises(VersionConflict):
        svc.update(d.id, {"lender": "Z"}, 1)
    assert svc.update(d.id, {"lender": "Other bank"}, 2).version == 2  # no-op
    with pytest.raises(InputError):
        svc.update(d.id, {"person_id": "p_nobody"}, 2)
    with pytest.raises(InputError):
        svc.update(d.id, {"lender": None}, 2)
    j = svc.update(d.id, {"person_id": None, "details": {}}, 2)
    assert j.person_id is None
    # kind change must keep plan/details consistent
    with pytest.raises(InputError):
        svc.update(d.id, {"kind": "student_loan"}, j.version)
    m = svc.update(d.id, {"kind": "mortgage", "details": {"rate_type": "svr"}}, j.version)
    assert m.details == {"rate_type": "svr"}


def test_a_settled_debt_must_be_reopened_before_editing(env):
    svc, *_ = env
    d = svc.create(_debt())
    s = svc.settle(d.id, d.version)
    with pytest.raises(InputError, match="Reopen it"):
        svc.update(d.id, {"lender": "Other bank"}, s.version)
