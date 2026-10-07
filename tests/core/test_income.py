from datetime import date

import pytest

from tuppence.core.accounts import AccountIn, AccountService
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.household import HouseholdService, PersonIn
from tuppence.core.income import IncomeIn, IncomeService
from tuppence.core.migrate import migrate
from tuppence.core.records import VersionConflict

DAY25 = {"type": "monthly_day", "day": 25, "adjust": "previous_working_day"}


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    hh = HouseholdService(db)
    a = hh.create_person(PersonIn(display_name="Alex Example", role="adult"))
    b = hh.create_person(PersonIn(display_name="Sam Example", role="adult"))
    accts = AccountService(db, hh)
    joint = accts.create(
        AccountIn(provider="monzo", kind="current", nickname="Joint", owner_ids=[a.id, b.id])
    )
    sams = accts.create(
        AccountIn(provider="monzo", kind="current", nickname="Sam", owner_ids=[b.id])
    )
    svc = IncomeService(db, hh, accts, today=lambda: date(2026, 12, 1))
    return svc, a, b, joint, sams


def _salary(person, account=None, amount="£2,345.67", rule=None, name="Salary"):
    return IncomeIn(
        person_id=person.id,
        kind="salary",
        name=name,
        net_amount=amount,
        account_id=None if account is None else account.id,
        pay_rule=rule or DAY25,
    )


def test_salary_into_joint_account(env):
    svc, a, _, joint, _ = env
    inc = svc.create(_salary(a, joint))
    assert inc.net_amount == "2345.67"
    assert inc.next_pay_date == date(2026, 12, 24)  # 25 Dec is a bank holiday
    assert inc.pay_rule_description.startswith("On the 25th")
    with svc.db.connection() as conn:
        assert conn.execute("SELECT net_pence FROM income_source").fetchone()[0] == 234567


def test_account_optional_and_must_belong_to_person(env):
    svc, a, _, _, sams = env
    assert svc.create(_salary(a)).account_id is None
    with pytest.raises(InputError, match="Choose an account that Alex Example owns or shares."):
        svc.create(_salary(a, sams))
    with pytest.raises(InputError):
        svc.create(_salary(a, amount="0"))


def test_closed_account_rejected(env):
    svc, _, b, _, sams = env
    svc.accounts.close(sams.id, sams.version)
    with pytest.raises(InputError):
        svc.create(_salary(b, sams))


def test_bad_rule_is_input_error(env):
    svc, a, *_ = env
    with pytest.raises(InputError):
        svc.create(_salary(a, rule={"type": "nonsense"}))
    with pytest.raises(InputError):
        svc.preview_rule({})


def test_update_and_end(env):
    svc, a, _, joint, sams = env
    inc = svc.create(_salary(a))
    upd = svc.update(inc.id, {"account_id": joint.id}, inc.version)
    assert upd.account_id == joint.id and upd.version == 2
    with pytest.raises(VersionConflict):
        svc.update(inc.id, {"name": "X"}, 1)
    with pytest.raises(InputError):
        svc.update(inc.id, {"account_id": sams.id}, 2)
    assert svc.update(inc.id, {"name": "Salary"}, 2).version == 2  # no-op
    ended = svc.end(inc.id, 2)
    assert ended.status == "ended" and ended.next_pay_date is None
    assert svc.list() == [] and len(svc.list(include_ended=True)) == 1


def test_preview_rule_five_dates_no_write(env):
    svc, *_ = env
    desc, dates = svc.preview_rule(DAY25)
    assert len(dates) == 5 and dates[0] == date(2026, 12, 24) and desc
    assert svc.list(include_ended=True) == []


def test_upcoming_merges_people_in_order(env):
    svc, a, b, *_ = env
    svc.create(_salary(a, name="Alex pay"))
    svc.create(_salary(b, name="Sam pay", rule={"type": "last_working_day"}))
    rows = svc.upcoming(date(2026, 12, 1), 35)
    assert [(d, i.name) for d, i in rows] == [
        (date(2026, 12, 24), "Alex pay"),
        (date(2026, 12, 31), "Sam pay"),
    ]
