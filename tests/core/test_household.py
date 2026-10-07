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
