from datetime import date

import pytest

from tuppence.ingest.check import check_document
from tuppence.ingest.importers.csv_layout import LayoutMismatch, parse_with_layout
from tuppence.ingest.registry import CsvLayout, LayoutRegistry, load_bank_pack
from tuppence.ingest.textprep import csv_document

# file, rows, skipped, opening, closing, sum of amounts (pence), last4 from a column
EXPECTED = [
    ("monzo", 9, 1, None, None, 125232, None),
    ("starling", 9, 0, 100000, 225232, 125232, None),
    ("hsbc", 9, 0, None, None, 125232, None),
    ("barclays", 9, 0, None, None, 125232, "5678"),
    ("lloyds-halifax", 10, 0, 100000, 224937, 124937, "4321"),
    ("natwest", 9, 0, 100000, 225232, 125232, "4321"),
    ("santander", 9, 0, 100000, 225232, 125232, None),
    ("nationwide", 9, 0, 100000, 225232, 125232, None),
    ("chase", 9, 0, 100000, 225232, 125232, None),
    ("revolut", 10, 2, 100000, 225182, 125182, None),
    ("amex", 5, 0, None, None, 5890, None),
    ("barclaycard", 6, 0, None, None, 5650, "4242"),
]


def load(fixtures, name):
    registry = LayoutRegistry(load_bank_pack())
    doc = csv_document(
        (fixtures / "csv" / f"{name}.csv").read_bytes(), sha256="x", known=registry.is_known_header
    )
    return doc, registry.match(doc)


@pytest.mark.parametrize("name,rows,skipped,opening,closing,total,last4", EXPECTED)
def test_every_uk_layout_imports_and_passes_check(
    fixtures, name, rows, skipped, opening, closing, total, last4
):
    doc, layout = load(fixtures, name)
    result = parse_with_layout(doc, layout)
    parsed = result.parsed
    assert result.problems == [] and check_document(doc, parsed) == []
    assert (len(parsed.rows), len(parsed.skipped)) == (rows, skipped)
    assert (parsed.opening_balance_pence, parsed.closing_balance_pence) == (opening, closing)
    assert sum(r.amount_pence for r in parsed.rows) == total
    assert result.last4 == last4
    assert parsed.importer == f"csv:{name}"
    assert [r.date for r in parsed.rows] == sorted(r.date for r in parsed.rows)


def test_newest_first_with_two_rows_on_one_day_is_put_in_order(fixtures):
    doc, layout = load(fixtures, "lloyds-halifax")
    parsed = parse_with_layout(doc, layout).parsed
    same_day = [
        (r.raw_description, r.amount_pence, r.balance_after_pence)
        for r in parsed.rows
        if r.date == date(2026, 10, 17)
    ]
    assert same_day == [("ACME PAYROLL LTD", 165000, 247507), ("CORNER CAFE", -295, 247212)]
    assert parsed.rows[0].sign_from == "Debit Amount" and parsed.rows[0].ref == "L11"


def test_monzo_details(fixtures):
    doc, layout = load(fixtures, "monzo")
    parsed = parse_with_layout(doc, layout).parsed
    first = parsed.rows[0]
    assert (first.ref, first.merchant, first.bank_category, first.bank_type) == (
        "L2",
        "Greenbasket Stores",
        "Groceries",
        "Card payment",
    )
    assert parsed.skipped[0].ref == "L5" and "zero-amount" in parsed.skipped[0].reason
    wages = next(r for r in parsed.rows if r.amount_pence == 165000)
    assert wages.raw_description == "Acme Payroll Ltd OCT WAGES"


def test_revolut_fee_and_skips(fixtures):
    doc, layout = load(fixtures, "revolut")
    parsed = parse_with_layout(doc, layout).parsed
    fee, rail = [r for r in parsed.rows if r.ref.startswith("L8")]
    assert (fee.ref, fee.amount_pence, fee.bank_type) == ("L8#fee", -50, "fee")
    assert (rail.amount_pence, rail.balance_after_pence) == (-2890, 244567)
    assert {s.ref: s.reason for s in parsed.skipped}["L11"].startswith("not completed")
    assert {s.ref for s in parsed.skipped} == {"L11", "L12"}


def test_card_exports_store_the_household_view(fixtures):
    doc, layout = load(fixtures, "barclaycard")
    parsed = parse_with_layout(doc, layout).parsed
    assert parsed.perspective == "card"
    assert [(r.raw_description, r.amount_pence) for r in parsed.rows][:4] == [
        ("Greenbasket Stores", -6420),
        ("Little Cafe", -415),
        ("Northline Rail", -2890),
        ("Harbour Pharmacy refund", 615),
    ]
    assert parsed.rows[-1].amount_pence == 15000


def test_bad_date_is_a_problem_not_a_crash(fixtures):
    registry = LayoutRegistry(load_bank_pack())
    text = (fixtures / "csv" / "starling.csv").read_text().replace("05/10/2026", "5th of Oct")
    doc = csv_document(text.encode(), sha256="x", known=registry.is_known_header)
    result = parse_with_layout(doc, registry.match(doc))
    assert result.problems == ['L4: can\'t read the date "5th of Oct"']
    assert "missing refs: L4" in check_document(doc, result.parsed)


def test_missing_column_is_reported(fixtures):
    doc, _ = load(fixtures, "starling")
    layout = CsvLayout(
        id="x",
        name="x",
        signature=["Date"],
        date="Date",
        description=["Payee"],
        amount="Amount (GBP)",
    )
    with pytest.raises(LayoutMismatch, match="Payee"):
        parse_with_layout(doc, layout)


@pytest.mark.parametrize(
    "extra",
    [
        {"category": "Cat"},
        {"merchant": "M"},
        {"type": "T"},
        {"account_number": "A"},
        {"fee": "F"},
        {"skip": [{"column": "State", "not_in": ["X"], "reason": "r"}]},
    ],
)
def test_every_named_column_must_exist(extra):
    from tuppence.core.errors import UserFacing

    doc = csv_document(b"Date,Desc,Amt\n05/01/2026,Tea,-2.50\n", sha256="x")
    layout = CsvLayout(
        id="u",
        name="My layout",
        signature=["Date", "Desc", "Amt"],
        date="Date",
        description=["Desc"],
        amount="Amt",
        **extra,
    )
    with pytest.raises(LayoutMismatch, match="My layout") as refused:
        parse_with_layout(doc, layout)
    assert isinstance(refused.value, UserFacing)
