from datetime import date

from tuppence.ingest.dedupe import (
    Existing,
    assign_fingerprints,
    fingerprint,
    normalise_description,
    plan_dedupe,
    similar_descriptions,
)
from tuppence.ingest.models import ParsedRow


def r(day: int, pence: int, desc: str, month: int = 10) -> ParsedRow:
    return ParsedRow(
        ref=f"L{day}",
        date=date(2026, month, day),
        amount_pence=pence,
        amount_text=str(abs(pence) / 100),
        raw_description=desc,
    )


def test_fingerprint_inputs():
    assert normalise_description("  GREENBASKET   Stores ") == "greenbasket stores"
    a = fingerprint("a_1", date(2026, 10, 1), -4218, "Greenbasket Stores", 0)
    assert len(a) == 24 and a == fingerprint(
        "a_1", date(2026, 10, 1), -4218, "GREENBASKET  STORES", 0
    )
    assert a != fingerprint("a_2", date(2026, 10, 1), -4218, "Greenbasket Stores", 0)
    assert a != fingerprint("a_1", date(2026, 10, 1), -4218, "Greenbasket Stores", 1)


def test_identical_lines_on_one_statement_get_occurrence_numbers():
    rows = [r(5, -280, "TFL TRAVEL"), r(5, -280, "TFL TRAVEL"), r(6, -280, "TFL TRAVEL")]
    out = assign_fingerprints(rows, "a_1")
    assert [occ for _, occ in out] == [0, 1, 0]
    assert len({fp for fp, _ in out}) == 3


def test_similar_descriptions():
    assert similar_descriptions("GREENBASKET STORES 0873 LONDON", "Greenbasket Stores")
    assert similar_descriptions("Card payment to Little Cafe", "LITTLE CAFE")
    assert not similar_descriptions("Little Cafe", "Northline Rail")
    assert not similar_descriptions("1234", "Little Cafe")


def test_same_month_in_another_format_is_not_counted_twice():
    stored_rows = [
        r(1, -4218, "Greenbasket Stores"),
        r(5, -340, "Little Cafe"),
        r(17, 165000, "Acme Payroll Ltd"),
    ]
    stored = [
        Existing(f"t_{i}", x.date, x.amount_pence, x.raw_description, fp)
        for i, (x, (fp, _)) in enumerate(
            zip(stored_rows, assign_fingerprints(stored_rows, "a_1"), strict=True)
        )
    ]
    new = [
        r(1, -4218, "Greenbasket Stores"),  # exact: same text
        r(5, -340, "CARD PAYMENT TO LITTLE CAFE ON 05 OCT"),  # same payment, other wording
        r(18, 165000, "ACME PAYROLL LTD BGC"),  # posted a day later in this format
        r(20, -2890, "Northline Rail"),
    ]  # genuinely new
    fps = [fp for fp, _ in assign_fingerprints(new, "a_1")]
    plan = plan_dedupe(new, fps, stored, window=(date(2026, 10, 1), date(2026, 10, 31)))
    assert plan.exact == [0]
    assert plan.similar == {1: "t_1", 2: "t_2"}
    assert plan.insert == [3]


def test_daily_coffees_in_the_next_month_are_never_merged():
    stored_rows = [r(30, -340, "Little Cafe"), r(31, -340, "Little Cafe")]
    stored = [
        Existing(f"t_{i}", x.date, x.amount_pence, x.raw_description, fp)
        for i, (x, (fp, _)) in enumerate(
            zip(stored_rows, assign_fingerprints(stored_rows, "a_1"), strict=True)
        )
    ]
    november = [r(1, -340, "Little Cafe", month=11), r(2, -340, "Little Cafe", month=11)]
    fps = [fp for fp, _ in assign_fingerprints(november, "a_1")]
    plan = plan_dedupe(november, fps, stored, window=(date(2026, 11, 1), date(2026, 11, 30)))
    assert plan.insert == [0, 1] and plan.similar == {} and plan.exact == []


def test_each_stored_row_matches_at_most_one_new_row():
    stored_rows = [r(5, -340, "Little Cafe")]
    stored = [
        Existing(
            "t_0",
            stored_rows[0].date,
            -340,
            "Little Cafe",
            assign_fingerprints(stored_rows, "a_1")[0][0],
        )
    ]
    new = [r(5, -340, "LITTLE CAFE LONDON"), r(5, -340, "LITTLE CAFE LONDON")]
    plan = plan_dedupe(
        new,
        [fp for fp, _ in assign_fingerprints(new, "a_1")],
        stored,
        window=(date(2026, 10, 1), date(2026, 10, 31)),
    )
    assert plan.similar == {0: "t_0"} and plan.insert == [1]
