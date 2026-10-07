import pytest

from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.household import (
    HouseholdPatch,
    HouseholdService,
    PersonIn,
    PersonPatch,
    normalise_district,
)
from tuppence.core.migrate import migrate
from tuppence.core.records import VersionConflict


@pytest.fixture
def hh(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return HouseholdService(db)


@pytest.mark.parametrize(
    "raw,expected", [("ls6", "LS6"), (" sw1a ", "SW1A"), ("M1", "M1"), ("cf10", "CF10")]
)
def test_normalise_district(raw, expected):
    assert normalise_district(raw) == expected


@pytest.mark.parametrize("raw", ["LS6 2AB", "ls62ab", "SW1A 1AA"])
def test_full_postcode_rejected_with_friendly_message(raw):
    with pytest.raises(InputError) as exc:
        normalise_district(raw)
    assert "first part of your postcode" in str(exc.value)


def test_nonsense_rejected():
    with pytest.raises(InputError):
        normalise_district("hello")


def test_household_singleton_and_update(hh):
    h = hh.get()
    assert h.version == 1 and h.currency == "GBP" and h.nation is None
    h2 = hh.update(HouseholdPatch(nation="wales", postcode_district="cf10"), expected_version=1)
    assert h2.nation == "wales" and h2.postcode_district == "CF10" and h2.version == 2
    with pytest.raises(VersionConflict):
        hh.update(HouseholdPatch(nation="england"), expected_version=1)


def test_people_lifecycle(hh):
    alex = hh.create_person(PersonIn(display_name="  Alex Example ", role="adult"))
    kid = hh.create_person(PersonIn(display_name="Kid A", role="child", birth_year=2019))
    assert alex.display_name == "Alex Example" and alex.id.startswith("p_")
    assert [p.display_name for p in hh.list_people()] == ["Alex Example", "Kid A"]
    renamed = hh.update_person(kid.id, PersonPatch(display_name="Kid Alpha"), expected_version=1)
    assert renamed.version == 2
    hh.retire_person(kid.id, expected_version=2)
    assert [p.display_name for p in hh.list_people()] == ["Alex Example"]
    assert len(hh.list_people(include_retired=True)) == 2


def test_person_validation():
    with pytest.raises(ValueError):
        PersonIn(display_name="", role="adult")
    with pytest.raises(ValueError):
        PersonIn(display_name="Kid", role="child", birth_year=1800)


def test_explicit_null_on_required_fields_rejected(hh):
    p = hh.create_person(PersonIn(display_name="Alex", role="adult"))
    with pytest.raises(InputError, match="display_name can't be empty"):
        hh.update_person(p.id, PersonPatch(display_name=None), expected_version=1)
    with pytest.raises(InputError, match="role can't be empty"):
        hh.update_person(p.id, PersonPatch(role=None), expected_version=1)
    with pytest.raises(InputError, match="period_mode can't be empty"):
        hh.update(HouseholdPatch(period_mode=None), expected_version=1)
    hh.update(HouseholdPatch(nation="wales"), expected_version=1)
    cleared = hh.update(HouseholdPatch(nation=None), expected_version=2)
    assert cleared.nation is None


def test_empty_changes_with_stale_version_conflicts(hh):
    p = hh.create_person(PersonIn(display_name="Alex", role="adult"))
    hh.update_person(p.id, PersonPatch(role="child"), expected_version=1)
    with pytest.raises(VersionConflict):
        hh.update_person(p.id, PersonPatch(), expected_version=1)
    assert hh.update_person(p.id, PersonPatch(), expected_version=2).version == 2
    hh.get()
    with pytest.raises(VersionConflict):
        hh.update(HouseholdPatch(), expected_version=5)


# --- Timeline is the source of truth for nation / postcode district (ruling R14) ---

from datetime import date, timedelta  # noqa: E402

from tuppence.core.timeline import Timeline  # noqa: E402


class Today:
    def __init__(self, day):
        self.day = day

    def __call__(self):
        return self.day


@pytest.fixture
def hh_tl(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    today = Today(date(2026, 10, 7))
    return HouseholdService(db, today=today), Timeline(db, today=today), today


def _spans(tl, attribute):
    return [(e.value, e.valid_from, e.valid_to) for e in tl.history("household", "1", attribute)]


def test_update_records_nation_and_district_on_the_timeline_from_today(hh_tl):
    hh, tl, today = hh_tl
    hh.update(HouseholdPatch(nation="wales", postcode_district="cf10"), expected_version=1)
    assert tl.as_of("household", "1", today()) == {"nation": "wales", "postcode_district": "CF10"}
    assert _spans(tl, "nation") == [("wales", today(), None)]
    # Saving the same values again (the form sends both fields) adds no new interval.
    hh.update(HouseholdPatch(nation="wales", postcode_district="CF10"), expected_version=2)
    assert _spans(tl, "nation") == [("wales", today(), None)]
    assert len(tl.history("household", "1", "postcode_district")) == 1
    # A later change from a later day closes the old interval.
    today.day = date(2026, 11, 1)
    hh.update(HouseholdPatch(nation="scotland"), expected_version=3)
    assert _spans(tl, "nation") == [
        ("wales", date(2026, 10, 7), date(2026, 11, 1)),
        ("scotland", date(2026, 11, 1), None),
    ]


def test_clearing_a_field_ends_it_on_the_timeline(hh_tl):
    hh, tl, today = hh_tl
    tl.set("household", "1", "nation", "wales", date(2026, 1, 1))
    hh.update(HouseholdPatch(postcode_district="ls6"), expected_version=2)
    hh.update(HouseholdPatch(nation=None, postcode_district=None), expected_version=3)
    h = hh.get()
    assert h.nation is None and h.postcode_district is None
    assert tl.as_of("household", "1", today()) == {}
    assert tl.value_as_of("household", "1", "nation", today() - timedelta(days=1)) == "wales"
    assert tl.history("household", "1", "postcode_district") == []  # set and cleared today


def test_past_timeline_entry_refreshes_the_household_row(hh_tl):
    hh, tl, _ = hh_tl
    assert hh.get().version == 1
    tl.set("household", "1", "nation", "scotland", date(2026, 1, 1))
    tl.set("household", "1", "postcode_district", "eh1", date(2026, 3, 1))
    h = hh.get()
    assert (h.nation, h.postcode_district) == ("scotland", "EH1") and h.version >= 2


def test_backdated_entry_before_a_newer_one_keeps_the_current_value(hh_tl):
    hh, tl, today = hh_tl
    hh.update(HouseholdPatch(nation="wales"), expected_version=1)
    tl.set("household", "1", "nation", "scotland", date(2026, 1, 1))
    assert hh.get().nation == "wales"
    assert tl.value_as_of("household", "1", "nation", today()) == "wales"


def test_future_entry_leaves_the_row_until_it_takes_effect(hh_tl):
    hh, tl, today = hh_tl
    hh.update(HouseholdPatch(nation="wales"), expected_version=1)
    tl.set("household", "1", "nation", "england", date(2027, 1, 1))
    h = hh.get()
    assert h.nation == "wales" and h.version == 2
    today.day = date(2027, 1, 1)
    h = hh.get()
    assert h.nation == "england" and h.version == 3
    with pytest.raises(VersionConflict):  # a tab still holding version 2 must reload
        hh.update(HouseholdPatch(nation="wales"), expected_version=2)
