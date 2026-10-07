from datetime import date

import pytest

from tuppence.core.accounts import AccountIn, AccountService
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.household import HouseholdPatch, HouseholdService, PersonIn
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


def test_rename_after_account_closed(env):
    svc, a, _, joint, sams = env
    inc = svc.create(_salary(a, joint))
    svc.accounts.close(joint.id, joint.version)
    renamed = svc.update(inc.id, {"name": "Alex salary"}, 1)
    assert renamed.name == "Alex salary" and renamed.account_id == joint.id
    with pytest.raises(InputError, match="owns or shares"):
        svc.update(inc.id, {"account_id": sams.id}, 2)  # relinking is still checked


def test_person_who_left_is_left_out(env):
    svc, a, b, *_ = env
    svc.create(_salary(a, name="Alex pay"))
    svc.create(_salary(b, name="Sam pay"))
    svc.household.retire_person(a.id, svc.household.get_person(a.id).version)
    assert [i.name for i in svc.list()] == ["Sam pay"]
    assert [i.name for _, i in svc.upcoming(date(2026, 12, 1))] == ["Sam pay"]
    everyone = {i.name: i for i in svc.list(include_ended=True)}
    assert everyone["Alex pay"].person_left and everyone["Alex pay"].next_pay_date is None
    assert everyone["Alex pay"].status == "active"  # kept, not ended
    assert not everyone["Sam pay"].person_left
    alex = everyone["Alex pay"]
    assert svc.update(alex.id, {"name": "Alex old pay"}, alex.version).name == "Alex old pay"


def test_calendar_assumed_flag(env):
    svc, a, *_ = env
    assert svc.create(_salary(a)).calendar_assumed is True
    assert svc.calendar_assumed()
    svc.household.update(HouseholdPatch(nation="scotland"), svc.household.get().version)
    assert svc.list()[0].calendar_assumed is False


def test_closing_an_account_returns_its_incomes_and_they_need_an_account_again(env):
    svc, a, b, joint, sams = env
    alex = svc.create(_salary(a, joint, name="Alex pay"))
    sam = svc.create(_salary(b, sams, name="Sam pay"))
    assert not alex.needs_account and not sam.needs_account
    closed = svc.accounts.close(joint.id, joint.version)
    assert [(r.id, r.name) for r in closed.affected_income] == [(alex.id, "Alex pay")]
    assert svc.get(alex.id).needs_account and not svc.get(sam.id).needs_account
    assert svc.create(_salary(a, name="No account yet")).needs_account


def test_removing_an_owner_returns_their_incomes(env):
    svc, a, b, joint, _ = env
    alex = svc.create(_salary(a, joint, name="Alex pay"))
    svc.create(_salary(b, joint, name="Sam pay"))
    updated = svc.accounts.update(joint.id, {"owner_ids": [b.id]}, joint.version)
    assert [r.name for r in updated.affected_income] == ["Alex pay"]
    assert svc.get(alex.id).needs_account
    assert [i.name for i in svc.list() if i.needs_account] == ["Alex pay"]
    renamed = svc.accounts.update(joint.id, {"nickname": "Bills"}, updated.version)
    assert renamed.affected_income == []


def test_status_changes_to_the_same_status_are_refused(env):
    svc, a, *_ = env
    inc = svc.create(_salary(a))
    ended = svc.end(inc.id, inc.version)
    with pytest.raises(InputError, match="already ended"):
        svc.end(inc.id, ended.version)


def test_pay_dates_use_the_nation_as_of_each_date(env):
    """St Andrew's Day (Mon 30 Nov 2026) is a bank holiday in Scotland only."""
    svc, a, *_ = env
    from tuppence.core.timeline import Timeline

    today = date(2026, 10, 7)
    svc.today = lambda: today
    svc.household.today = lambda: today
    rule = {"type": "monthly_day", "day": 30, "adjust": "previous_working_day"}
    inc = svc.create(_salary(a, rule=rule))
    svc.household.update(HouseholdPatch(nation="england"), svc.household.get().version)
    Timeline(svc.db, today=lambda: today).set(
        "household", "1", "nation", "scotland", date(2026, 11, 1)
    )
    assert svc.household.get().nation == "england"  # the move is still in the future
    dates = [d for d, _ in svc.upcoming(today, 60)]
    assert dates == [date(2026, 10, 30), date(2026, 11, 27)]  # Nov follows Scotland's calendar
    _, preview = svc.preview_rule(rule)
    assert preview[:2] == [date(2026, 10, 30), date(2026, 11, 27)]
    svc.today = lambda: date(2026, 11, 1)
    assert svc.get(inc.id).next_pay_date == date(2026, 11, 27)
