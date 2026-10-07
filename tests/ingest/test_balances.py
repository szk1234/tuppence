import pytest

from ingest.statements import HEAD, TABLE
from tuppence.ingest.balances import local_balances, repair_signs
from tuppence.ingest.models import ParsedRow, ParsedStatement, SkippedLine
from tuppence.ingest.textprep import pages_document


def doc_with(*summary: str, rows=("01/10/2026 SHOP 5.00 95.00",)):
    return pages_document([[*HEAD, *summary, TABLE, *rows]], sha256="x", kind="pdf")


@pytest.mark.parametrize(
    ("lines", "perspective", "opening", "closing"),
    [
        (["Opening balance £1,000.00", "Closing balance £1,857.82"], "household", 100000, 185782),
        (
            ["Opening balance on 01/10/2026 £1,000.00", "Closing balance on 31/10/2026 £1,857.82"],
            "household",
            100000,
            185782,
        ),
        (
            [
                "Opening balance (on 1 Oct 2026) £1,000.00",
                "Closing balance at 31 October 2026 1,857.82",
            ],
            "household",
            100000,
            185782,
        ),
        (
            ["Opening balance 1,000.00 CR", "Closing balance 1,857.82 CR"],
            "household",
            100000,
            185782,
        ),
        (["Opening balance (100.00)", "Closing balance 20.00 DR"], "household", -10000, -2000),
        (["Opening balance 100.00-", "Closing balance -20.00"], "household", -10000, -2000),
        (
            ["Balance brought forward £500.00", "Balance carried forward £450.00"],
            "household",
            50000,
            45000,
        ),
        (["Previous balance £842.16", "New balance £909.85"], "card", 84216, 90985),
        (["Previous balance 100.00 CR", "New balance 20.00 CR"], "card", -10000, -2000),
        (["Previous balance (100.00)", "New balance 20.00 DR"], "card", -10000, 2000),
        (["Opening balance £1,000.00   Closing balance £1,857.82"], "household", 100000, 185782),
    ],
)
def test_balance_phrasings_and_signs(lines, perspective, opening, closing):
    found = local_balances(doc_with(*lines), perspective=perspective)
    assert (found.opening, found.closing) == (opening, closing)


def test_conflicting_or_cluttered_figures_are_left_to_the_fallback():
    conflicting = doc_with("Opening balance £1,000.00", "Opening balance £900.00")
    assert local_balances(conflicting, perspective="household").opening is None
    cluttered = doc_with("Closing balance Money in 900.00 120.00")
    assert local_balances(cluttered, perspective="household").closing is None


def row(ref, pence, text, after, **kw):
    from datetime import date

    return ParsedRow(
        ref=ref,
        date=date(2026, 10, 1),
        amount_pence=pence,
        amount_text=text,
        raw_description="x",
        balance_after_pence=after,
        **kw,
    )


def statement(*rows, skipped=()):
    return ParsedStatement(importer="t", rows=list(rows), skipped=list(skipped))


def test_sign_comes_from_the_balance_difference():
    doc = pages_document(
        [[TABLE, "01/10/2026 ACME 900.00 1,900.00", "03/10/2026 SHOP 42.18 1,857.82"]],
        sha256="x",
        kind="pdf",
    )
    parsed = statement(row("P1L2", -90000, "900.00", 190000), row("P1L3", -4218, "42.18", 185782))
    result = repair_signs(doc, parsed, opening=100000, level="full")
    assert result.errors == [] and result.repaired == ["P1L2"]
    assert parsed.rows[0].amount_pence == 90000 and parsed.rows[0].sign_from == "Paid in"
    assert parsed.rows[1].amount_pence == -4218


def test_a_printed_sign_is_never_overruled():
    doc = pages_document([[TABLE, "01/10/2026 ACME -900.00 1,900.00"]], sha256="x", kind="pdf")
    parsed = statement(row("P1L2", -90000, "-900.00", 190000))
    assert repair_signs(doc, parsed, opening=100000, level="full").repaired == []
    assert parsed.rows[0].amount_pence == -90000


def test_a_brought_forward_line_gives_the_balance_before_the_first_row():
    doc = pages_document(
        [[TABLE, "Balance brought forward 1,000.00", "01/10/2026 ACME 900.00 1,900.00"]],
        sha256="x",
        kind="pdf",
    )
    parsed = statement(
        row("P1L3", -90000, "900.00", 190000), skipped=[SkippedLine(ref="P1L2", reason="bf")]
    )
    result = repair_signs(doc, parsed, opening=None, level="full")
    assert result.repaired == ["P1L3"] and parsed.rows[0].amount_pence == 90000


def test_an_unprovable_first_row_is_reported():
    doc = pages_document([[TABLE, "01/10/2026 ACME 900.00 1,900.00"]], sha256="x", kind="pdf")
    parsed = statement(row("P1L2", 90000, "900.00", 190000))
    result = repair_signs(doc, parsed, opening=None, level="full")
    assert result.repaired == [] and "can't tell whether" in result.errors[0]
    assert repair_signs(doc, parsed, opening=None, level="screenshot").errors == []
