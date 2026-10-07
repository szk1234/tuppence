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
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO person (id, display_name, role, created_at, updated_at)"
            " VALUES ('p1', 'Alex', 'adult', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
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


def test_household_district_normalised_and_full_postcode_rejected(tl):
    e = tl.set("household", "1", "postcode_district", "ls6", date(2026, 1, 1))
    assert e.value == "LS6"
    with pytest.raises(InputError, match="first part of your postcode"):
        tl.set("household", "1", "postcode_district", "LS6 2AB", date(2026, 2, 1))
    assert [h.value for h in tl.history("household", "1", "postcode_district")] == ["LS6"]


def test_subject_must_exist(tl):
    from tuppence.core.records import NotFound

    with pytest.raises(NotFound):
        tl.set("person", "p_nobody", "household_member", True, date(2026, 1, 1))
    with pytest.raises(NotFound):
        tl.set("household", "2", "nation", "wales", date(2026, 1, 1))


def test_home_attributes_validated(tl):
    tl.set("household", "1", "housing_tenure", "renting", date(2025, 1, 1))
    tl.set("household", "1", "council_tax_band", "D", date(2025, 1, 1))
    tl.set("household", "1", "housing_monthly_pence", "950.00", date(2025, 1, 1))
    assert tl.value_as_of("household", "1", "housing_monthly_pence", date(2025, 1, 1)) == 95000
    for attribute, bad in [
        ("housing_tenure", "squatting"),
        ("council_tax_band", "Z"),
        ("bedrooms", 21),
        ("housing_monthly_pence", -1),
        ("housing_monthly_pence", 95000),
        ("housing_monthly_pence", "99999999999999"),
    ]:
        with pytest.raises(InputError):
            tl.set("household", "1", attribute, bad, date(2025, 1, 1))


def test_expected_current_conflict_and_match(tl):
    from tuppence.core.records import VersionConflict

    tl.set("household", "1", "bedrooms", 2, date(2025, 1, 1))
    with pytest.raises(VersionConflict):
        tl.set("household", "1", "bedrooms", 3, date(2026, 1, 1), expected_current=None)
    with pytest.raises(VersionConflict):
        tl.set("household", "1", "bedrooms", 3, date(2026, 1, 1), expected_current=5)
    tl.set("household", "1", "bedrooms", 3, date(2026, 1, 1), expected_current=2)
    tl.set("household", "1", "council_tax_band", "C", date(2026, 1, 1), expected_current=None)


def test_delete_extends_previous_and_checks_version(tl):
    from tuppence.core.records import NotFound, VersionConflict

    tl.set("household", "1", "bedrooms", 2, date(2024, 1, 1))
    mid = tl.set("household", "1", "bedrooms", 3, date(2025, 1, 1))
    tl.set("household", "1", "bedrooms", 4, date(2026, 1, 1))
    with pytest.raises(VersionConflict):
        tl.delete(mid.id, mid.version + 1)
    tl.delete(mid.id, mid.version)
    hist = tl.history("household", "1", "bedrooms")
    assert [(h.value, h.valid_to) for h in hist] == [(2, date(2026, 1, 1)), (4, None)]
    with pytest.raises(NotFound):
        tl.delete(mid.id, 1)


def test_history_is_chronological(tl):
    tl.set("household", "1", "bedrooms", 2, date(2026, 1, 1))
    tl.set("household", "1", "housing_tenure", "owned", date(2024, 1, 1))
    tl.set("household", "1", "bedrooms", 1, date(2025, 1, 1))
    hist = tl.history("household", "1")
    assert [h.valid_from for h in hist] == sorted(h.valid_from for h in hist)


def _row(tl):
    with tl.db.connection() as c:
        return c.execute("SELECT nation, postcode_district FROM household WHERE id = 1").fetchone()


def test_deleting_only_entry_clears_household_row(tl):
    e = tl.set("household", "1", "postcode_district", "LS6", date(2020, 1, 1))
    assert _row(tl)["postcode_district"] == "LS6"
    tl.delete(e.id, e.version)
    assert _row(tl)["postcode_district"] is None


def test_deleting_earlier_entry_keeps_later_value(tl):
    first = tl.set("household", "1", "nation", "england", date(2020, 1, 1))
    tl.set("household", "1", "nation", "wales", date(2021, 1, 1))
    tl.delete(first.id, first.version)
    assert _row(tl)["nation"] == "wales"


def test_neighbour_version_unchanged_by_write_or_delete(tl):
    a = tl.set("household", "1", "bedrooms", 2, date(2024, 1, 1))
    b = tl.set("household", "1", "bedrooms", 3, date(2025, 1, 1))
    assert tl.history("household", "1", "bedrooms")[0].version == a.version
    tl.delete(b.id, b.version)
    assert tl.history("household", "1", "bedrooms")[0].version == a.version


def test_income_band_prefer_not_to_say_replaces_the_band(tl):
    tl.set("person", "p1", "income_band", "12570_50270", date(2026, 1, 1))
    tl.set(
        "person",
        "p1",
        "income_band",
        "prefer_not_to_say",
        date(2026, 6, 1),
        expected_current="12570_50270",
    )
    assert tl.value_as_of("person", "p1", "income_band", date(2026, 6, 1)) == "prefer_not_to_say"
    assert tl.value_as_of("person", "p1", "income_band", date(2026, 5, 31)) == "12570_50270"
    # Same day: the band is replaced, not kept alongside.
    tl.set("person", "p1", "income_band", "50270_100000", date(2026, 7, 1))
    tl.set("person", "p1", "income_band", "prefer_not_to_say", date(2026, 7, 1))
    assert [h.value for h in tl.history("person", "p1", "income_band")] == [
        "12570_50270",
        "prefer_not_to_say",
        "prefer_not_to_say",
    ]


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("income_band", None, "Choose an income band, or Prefer not to say."),
        ("income_band", "lots", "Choose an income band, or Prefer not to say."),
        ("employment_status", None, "Choose a work status."),
        ("employment_status", "astronaut", "Choose a work status."),
    ],
)
def test_bad_person_values_get_plain_messages(tl, attribute, value, message):
    with pytest.raises(InputError) as caught:
        tl.set("person", "p1", attribute, value, date(2026, 1, 1))
    assert str(caught.value) == message


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("nation", "atlantis"),
        ("housing_tenure", None),
        ("bedrooms", 21),
        ("council_tax_band", "Z"),
    ],
)
def test_bad_household_values_never_show_the_raw_validation_text(tl, attribute, value):
    with pytest.raises(InputError) as caught:
        tl.set("household", "1", attribute, value, date(2026, 1, 1))
    assert "Input should" not in str(caught.value) and "_" not in str(caught.value)
