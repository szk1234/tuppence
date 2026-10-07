from datetime import date

import pytest

from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.migrate import migrate
from tuppence.core.timeline import Timeline


@pytest.fixture
def tl(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return Timeline(db)


def test_change_of_employment_closes_previous_interval(tl):
    tl.set("person", "p1", "employment_status", "employed", date(2024, 1, 1))
    tl.set("person", "p1", "employment_status", "not_working", date(2026, 5, 1))
    assert tl.value_as_of("person", "p1", "employment_status", date(2026, 4, 30)) == "employed"
    assert tl.value_as_of("person", "p1", "employment_status", date(2026, 5, 1)) == "not_working"
    hist = tl.history("person", "p1", "employment_status")
    assert [(h.valid_from, h.valid_to) for h in hist] == [
        (date(2024, 1, 1), date(2026, 5, 1)),
        (date(2026, 5, 1), None),
    ]


def test_backdated_change_inserted_between_entries(tl):
    tl.set("person", "p1", "employment_status", "employed", date(2024, 1, 1))
    tl.set("person", "p1", "employment_status", "retired", date(2027, 1, 1))
    tl.set("person", "p1", "employment_status", "self_employed", date(2025, 6, 1))
    hist = tl.history("person", "p1", "employment_status")
    assert [(h.value, h.valid_from, h.valid_to) for h in hist] == [
        ("employed", date(2024, 1, 1), date(2025, 6, 1)),
        ("self_employed", date(2025, 6, 1), date(2027, 1, 1)),
        ("retired", date(2027, 1, 1), None),
    ]


def test_same_day_set_replaces_value(tl):
    tl.set("person", "p1", "income_band", "12570_50270", date(2026, 4, 6))
    tl.set("person", "p1", "income_band", "50270_100000", date(2026, 4, 6))
    hist = tl.history("person", "p1", "income_band")
    assert len(hist) == 1 and hist[0].value == "50270_100000"


def test_before_first_entry_is_none_and_as_of_collects(tl):
    tl.set("person", "p1", "employment_status", "employed", date(2024, 1, 1))
    tl.set("person", "p1", "household_member", True, date(2024, 1, 1))
    assert tl.value_as_of("person", "p1", "employment_status", date(2023, 12, 31)) is None
    assert tl.as_of("person", "p1", date(2025, 1, 1)) == {
        "employment_status": "employed",
        "household_member": True,
    }


def test_end_closes_open_interval(tl):
    tl.set("person", "p1", "household_member", True, date(2024, 1, 1))
    tl.end("person", "p1", "household_member", date(2026, 3, 1))
    assert tl.value_as_of("person", "p1", "household_member", date(2026, 3, 1)) is None
    assert tl.value_as_of("person", "p1", "household_member", date(2026, 2, 28)) is True


def test_rejects_unknown_attribute_and_bad_value(tl):
    with pytest.raises(InputError):
        tl.set("person", "p1", "shoe_size", 9, date(2026, 1, 1))
    with pytest.raises(InputError):
        tl.set("person", "p1", "employment_status", "astronaut", date(2026, 1, 1))
