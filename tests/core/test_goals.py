from datetime import date

import pytest

from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.goals import GoalIn, GoalService
from tuppence.core.migrate import migrate

TODAY = date(2026, 10, 7)


@pytest.fixture
def svc(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return GoalService(db, today=lambda: TODAY)


def test_create_and_pounds(svc):
    g = svc.create(
        GoalIn(name="Holiday", kind="holiday", target_amount="£2,000", target_date=date(2027, 6, 1))
    )
    assert g.target_amount == "2000.00" and g.saved_amount == "0.00" and g.priority == 2
    with svc.db.connection() as conn:
        assert conn.execute("SELECT target_pence FROM goal").fetchone()[0] == 200000


def test_past_or_today_target_date_rejected(svc):
    with pytest.raises(InputError, match="future"):
        svc.create(GoalIn(name="X", kind="other", target_date=date(2026, 1, 1)))
    with pytest.raises(InputError, match="future"):
        svc.create(GoalIn(name="X", kind="other", target_date=TODAY))
    with pytest.raises(InputError):
        svc.create(GoalIn(name="X", kind="other", target_amount="0"))


def test_update_validates_only_changes(svc):
    g = svc.create(GoalIn(name="Car", kind="car", target_date=date(2026, 12, 1)))
    later = GoalService(svc.db, today=lambda: date(2027, 1, 1))  # the date has now passed
    r = later.update(g.id, {"name": "New car"}, g.version)
    assert r.name == "New car"
    with pytest.raises(InputError, match="future"):
        later.update(g.id, {"target_date": "2026-12-15"}, r.version)
    ok = later.update(g.id, {"target_date": "2027-03-01", "saved_amount": "50"}, r.version)
    assert ok.saved_amount == "50.00"
    with pytest.raises(InputError):
        later.update(g.id, {"name": None}, ok.version)


def test_emergency_fund_suggestion_flips(svc):
    assert svc.suggest_emergency_fund() is True
    g = svc.create(GoalIn(name="Rainy day", kind="emergency_fund", target_amount="3000"))
    assert svc.suggest_emergency_fund() is False
    svc.set_status(g.id, "abandoned", g.version)
    assert svc.suggest_emergency_fund() is True
    assert svc.list() == [] and len(svc.list(include_closed=True)) == 1


def test_bad_status(svc):
    g = svc.create(GoalIn(name="X", kind="other"))
    with pytest.raises(InputError):
        svc.set_status(g.id, "nope", g.version)


def test_same_status_changes_and_edits_while_closed_are_refused(svc):
    g = svc.create(GoalIn(name="Car", kind="car"))
    with pytest.raises(InputError, match="already active"):
        svc.set_status(g.id, "active", g.version)
    done = svc.set_status(g.id, "achieved", g.version)
    with pytest.raises(InputError, match="already achieved"):
        svc.set_status(g.id, "achieved", done.version)
    with pytest.raises(InputError, match="Reopen it"):
        svc.update(g.id, {"name": "Van"}, done.version)
    dropped = svc.set_status(g.id, "abandoned", done.version)
    assert dropped.status == "abandoned"
    assert svc.set_status(g.id, "active", dropped.version).status == "active"
