"""Which way round a learned CSV layout reads money in and money out.

A single amount column adds up (with a balance column too) whichever way round it is read, so
the account type and the rows themselves are the evidence. All values invented.
"""

import datetime as dt
import json

import pytest

from ingest.helpers import budget, use_local_model
from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.identify import identify
from tuppence.ingest.parse import ReaderLimits, parse_document
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack

RIGHT = {
    "date_column": "Date",
    "description_columns": ["Narrative"],
    "merchant_column": None,
    "amount_column": "Amount",
    "money_out_column": None,
    "money_in_column": None,
    "amounts_are": "money_out_negative",
    "balance_column": "Balance",
    "category_column": None,
    "type_column": None,
}
INVERTED = {**RIGHT, "amounts_are": "purchases_positive"}


def csv(rows, *, header="Date,Narrative,Amount,Balance", balance=100.0):
    lines = [header]
    for i, (desc, amount) in enumerate(rows, start=1):
        balance += amount
        lines.append(f"{i:02d}/10/2026,{desc},{amount:.2f},{balance:.2f}")
    return ("\n".join(lines) + "\n").encode()


def parse_csv(services, tmp_path, registry, account_kind, data, name="export.csv"):
    window = services.router.chain_for("read")[0][1].context_window
    path = tmp_path / name
    path.write_bytes(data)
    pack = load_bank_pack()
    doc = extract_document(
        path, "csv", sha256=name, limits=ExtractLimits(), known_header=registry.is_known_header
    )
    evidence = identify(doc, pack=pack, registry=registry, key=b"k")
    out = parse_document(
        doc,
        path,
        evidence,
        account_kind,
        registry=registry,
        llm=services.llm,
        run=budget(),
        context_window=window,
        today=dt.date(2026, 11, 1),
        limits=ReaderLimits(),
    )
    return out, doc


MIXED = [  # 2 of 5 rows money in: read inverted, 3 of 5 would be money in
    ("ACME PAYROLL", 900.0),
    ("TFR FROM SAVINGS", 100.0),
    ("SHOP", -20.0),
    ("CAFE", -3.0),
    ("RENT", -500.0),
]
BILLS_POT = [  # a joint bills account: mostly money in, and right
    ("TFR FROM PARTNER", 400.0),
    ("TFR FROM ALEX", 400.0),
    ("TFR FROM PARTNER", 50.0),
    ("TFR FROM ALEX", 50.0),
    ("DD COUNCIL TAX", -150.0),
    ("DD ENERGY CO", -90.0),
]


@pytest.mark.parametrize("kind", ["current", "savings"])
@pytest.mark.parametrize("rows", [MIXED, BILLS_POT], ids=["mixed", "bills"])
def test_purchases_positive_on_a_bank_account_is_refused(ingest_env, tmp_path, kind, rows):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(INVERTED)}] * 3
    out, doc = parse_csv(services, tmp_path, registry, kind, csv(rows))
    assert out.parsed.rows == [] and out.pending_layout is None
    assert registry.match(doc) is None
    assert any("purchases_positive" in e for e in out.errors)
    # the model was told why, by column and convention only
    feedback = scripted.requests[1]["messages"][-1]["content"].split("didn't work:")[1]
    assert "purchases_positive" in feedback and "PAYROLL" not in feedback


def test_a_refused_convention_can_be_corrected_on_the_retry(ingest_env, tmp_path):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(INVERTED)}, {"content": json.dumps(RIGHT)}]
    out, doc = parse_csv(services, tmp_path, registry, "current", csv(MIXED))
    assert out.errors == [] and registry.match(doc) is not None
    assert [r.amount_pence for r in out.parsed.rows] == [90000, 10000, -2000, -300, -50000]


@pytest.mark.parametrize("kind", ["current", "savings"])
def test_an_income_heavy_bank_account_is_not_second_guessed(ingest_env, tmp_path, kind):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(RIGHT)}]
    out, doc = parse_csv(services, tmp_path, registry, kind, csv(BILLS_POT), "a.csv")
    assert out.errors == [] and out.pending_layout is None and registry.match(doc) is not None
    again, _ = parse_csv(services, tmp_path, registry, kind, csv(BILLS_POT[:4]), "b.csv")
    assert again.errors == [] and len(scripted.requests) == 1  # reused, no AI, no doubt


PAYMENTS_ONLY = [("PAYMENT RECEIVED - THANK YOU", -100.0)] * 4  # a balance-transfer card


def test_a_card_whose_rows_confirm_the_signs_is_kept(ingest_env, tmp_path):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(INVERTED)}]
    data = csv(PAYMENTS_ONLY, balance=1000.0)
    out, doc = parse_csv(services, tmp_path, registry, "credit_card", data)
    assert [r.amount_pence for r in out.parsed.rows] == [10000] * 4
    assert out.errors == [] and out.pending_layout is None and registry.match(doc) is not None


def test_a_card_whose_rows_contradict_the_signs_is_held_back(ingest_env, tmp_path):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(RIGHT)}]  # but this card prints purchases positive
    rows = [("GREENBASKET STORES", 42.18), ("PAYMENT RECEIVED - THANK YOU", -100.0)]
    out, doc = parse_csv(services, tmp_path, registry, "credit_card", csv(rows))
    assert any("back to front" in e for e in out.errors)
    assert out.pending_layout is not None and registry.match(doc) is None


def test_a_type_column_is_evidence_too(ingest_env, tmp_path):
    """A Type column of PURCHASE rows is evidence. (A column of DEBIT/CREDIT or DR/CR markers is
    more than evidence: it gives every amount its direction, tested below.)"""
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    header = "Date,Narrative,Type,Amount,Balance"
    data = (
        f"{header}\n01/10/2026,GREENBASKET STORES,PURCHASE,42.18,142.18\n"
        "02/10/2026,LITTLE CAFE,PURCHASE,3.40,145.58\n"
    ).encode()
    scripted.replies = [{"content": json.dumps({**RIGHT, "type_column": "Type"})}]
    out, doc = parse_csv(services, tmp_path, registry, "credit_card", data)
    assert any("back to front" in e for e in out.errors) and registry.match(doc) is None


def test_a_new_card_layout_without_evidence_waits_for_the_person(ingest_env, tmp_path):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(INVERTED)}]
    rows = [("GREENBASKET STORES", 42.18), ("LITTLE CAFE", 3.40)]
    out, doc = parse_csv(services, tmp_path, registry, "credit_card", csv(rows))
    assert any("which way round" in e for e in out.errors)
    assert out.pending_layout is not None and registry.match(doc) is None


def test_a_remembered_card_layout_is_not_trusted_for_a_bank_account(ingest_env, tmp_path):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(INVERTED)}]
    out, doc = parse_csv(
        services, tmp_path, registry, "credit_card", csv(PAYMENTS_ONLY, balance=900.0), "a.csv"
    )
    assert out.errors == [] and registry.match(doc) is not None
    again, _ = parse_csv(services, tmp_path, registry, "current", csv(MIXED), "b.csv")
    assert any("back to front" in e for e in again.errors) and len(scripted.requests) == 1


def test_two_columns_swapped_is_refused_by_check(ingest_env, tmp_path):
    services, scripted = ingest_env
    registry = LayoutRegistry(load_bank_pack())
    use_local_model(services)
    data = (
        b"Date,Details,Money out,Money in\n"
        b"01/10/2026,A,42.18,\n02/10/2026,B,3.40,\n03/10/2026,C,10.00,\n"
        b"04/10/2026,D,31.15,\n05/10/2026,E,,900.00\n"
    )
    swapped = {
        **RIGHT,
        "amount_column": None,
        "money_out_column": "Money in",
        "money_in_column": "Money out",
        "balance_column": None,
        "description_columns": ["Details"],
    }
    scripted.replies = [{"content": json.dumps(swapped)}] * 3
    out, doc = parse_csv(services, tmp_path, registry, "current", data)
    # the column headings contradict the signs, so Check refuses it and nothing is kept
    assert any("wrong way round" in e for e in out.errors) and registry.match(doc) is None


# --- a sign source is required (R-M3-17) ------------------------------------------------------

TYPED = [  # an unsigned amount column; the direction is only in a DR/CR column
    ("GREENBASKET STORES", "DR", "42.18"),
    ("ACME PAYROLL", "CR", "900.00"),
    ("LITTLE CAFE", "DR", "3.40"),
    ("CITY WATER", "DR", "31.15"),
    ("HOMEWARE DIRECT", "DR", "43.27"),
    ("NORTHLINE RAIL", "DR", "12.80"),
]
TYPED_PENCE = [-4218, 90000, -340, -3115, -4327, -1280]


def typed_csv(rows=TYPED, *, header="Date,Description,Type,Amount", words=("DR", "CR")):
    swap = {"DR": words[0], "CR": words[1]}
    lines = [header] + [
        f"{i:02d}/10/2026,{desc},{swap[kind]},{amount}"
        for i, (desc, kind, amount) in enumerate(rows, start=1)
    ]
    return ("\n".join(lines) + "\n").encode()


def single_amount(**changes):
    return {**RIGHT, "description_columns": ["Description"], "balance_column": None, **changes}


@pytest.mark.parametrize("kind", ["current", "savings"])
@pytest.mark.parametrize("type_column", [None, "Type"], ids=["type-unmapped", "type-mapped"])
def test_a_dr_cr_column_is_the_sign_source(ingest_env, tmp_path, kind, type_column):
    """The re-review's probe (csvrun.py): every row was stored as money in and the layout kept."""
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(single_amount(type_column=type_column))}]
    out, doc = parse_csv(services, tmp_path, registry, kind, typed_csv())
    assert [r.amount_pence for r in out.parsed.rows] == TYPED_PENCE
    assert out.errors == [] and len(scripted.requests) == 1
    saved = registry.match(doc)
    assert saved is not None and saved.direction == "Type"
    again, _ = parse_csv(services, tmp_path, registry, kind, typed_csv(TYPED[:3]), "b.csv")
    assert [r.amount_pence for r in again.parsed.rows] == TYPED_PENCE[:3]
    assert again.errors == [] and len(scripted.requests) == 1  # reused, no AI


@pytest.mark.parametrize(
    ("header", "words"),
    [
        ("Date,Description,DR/CR,Amount", ("DR", "CR")),
        ("Date,Description,Debit/Credit,Amount", ("Debit", "Credit")),
        ("Date,Description,Type,Amount", ("D", "C")),
        ("Date,Description,Type,Amount", ("dr", "cr")),
        ("Date,Description,Amount,Dr/Cr", ("Dr.", "Cr.")),
    ],
)
def test_dr_cr_columns_are_found_by_their_values(ingest_env, tmp_path, header, words):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    if header.endswith("Dr/Cr"):
        lines = [header] + [
            f"{i:02d}/10/2026,{d},{a},{words[0] if k == 'DR' else words[1]}"
            for i, (d, k, a) in enumerate(TYPED, start=1)
        ]
        data = ("\n".join(lines) + "\n").encode()
    else:
        data = typed_csv(header=header, words=words)
    scripted.replies = [{"content": json.dumps(single_amount())}]
    out, doc = parse_csv(services, tmp_path, registry, "current", data)
    assert [r.amount_pence for r in out.parsed.rows] == TYPED_PENCE
    assert out.errors == [] and registry.match(doc) is not None


@pytest.mark.parametrize("words", [("DR", "CR"), ("D", "C"), ("Debit", "Credit")])
def test_a_card_with_a_dr_cr_column_takes_its_signs_from_it(ingest_env, tmp_path, words):
    """N9: the card's own answer is used on the first request (no fallback to another)."""
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [
        {"content": json.dumps(single_amount(amounts_are="purchases_positive"))}
    ] * 3
    out, doc = parse_csv(services, tmp_path, registry, "credit_card", typed_csv(words=words))
    assert [r.amount_pence for r in out.parsed.rows] == TYPED_PENCE
    assert out.errors == [] and len(scripted.requests) == 1
    saved = registry.match(doc)
    assert saved is not None and saved.perspective == "card" and saved.direction == "Type"


UNSIGNED = [("GREENBASKET STORES", 42.18), ("ACME PAYROLL", 900.0), ("LITTLE CAFE", 3.40)]


def unsigned_csv(rows=UNSIGNED, *, header="Date,Description,Amount"):
    lines = [header] + [
        f"{i:02d}/10/2026,{desc},{amount:.2f}" for i, (desc, amount) in enumerate(rows, start=1)
    ]
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.parametrize("kind", ["current", "savings"])
def test_an_amount_column_with_no_sign_source_is_refused(ingest_env, tmp_path, kind):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(single_amount())}] * 3
    out, doc = parse_csv(services, tmp_path, registry, kind, unsigned_csv())
    # not imported as all money in: the statement needs review and nothing is remembered
    assert any(
        "money in can't be told from money out" in e and e.endswith("check the signs.")
        for e in out.errors
    )
    assert registry.match(doc) is None and out.pending_layout is None
    # the model was told, in fixed words with no value from the file
    feedback = scripted.requests[1]["messages"][-1]["content"].split("didn't work:")[1]
    assert "DR/CR column" in feedback
    assert not any(value in feedback for value in ("GREENBASKET", "42.18", "900.00"))


def test_a_refused_amount_column_can_be_corrected_with_two_columns(ingest_env, tmp_path):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    data = (
        b"Date,Description,Paid out,Paid in\n"
        b"01/10/2026,GREENBASKET STORES,42.18,\n02/10/2026,ACME PAYROLL,,900.00\n"
        b"03/10/2026,LITTLE CAFE,3.40,\n"
    )
    wrong = single_amount(amount_column="Paid out")  # one of the two columns, always positive
    right = single_amount(
        amount_column=None, money_out_column="Paid out", money_in_column="Paid in"
    )
    scripted.replies = [{"content": json.dumps(wrong)}, {"content": json.dumps(right)}]
    out, doc = parse_csv(services, tmp_path, registry, "current", data)
    assert [r.amount_pence for r in out.parsed.rows] == [-4218, 90000, -340]
    assert out.errors == [] and registry.match(doc) is not None


def test_a_missing_dr_cr_marker_on_a_later_file_is_reported():
    from tuppence.ingest.importers.csv_layout import parse_with_layout
    from tuppence.ingest.registry import CsvLayout
    from tuppence.ingest.textprep import csv_document

    layout = CsvLayout(
        id="learned-x",
        name="x",
        source="learned",
        signature=["Date", "Description", "Type", "Amount"],
        date="Date",
        description=["Description"],
        amount="Amount",
        direction="Type",
    )
    doc = csv_document(
        b"Date,Description,Type,Amount\n01/10/2026,SHOP,DR,4.00\n02/10/2026,CAFE,,3.00\n",
        sha256="x",
    )
    result = parse_with_layout(doc, layout)
    assert [r.amount_pence for r in result.parsed.rows] == [-400]
    assert [r.sign_from for r in result.parsed.rows] == ["DR"]
    assert result.problems == ["L3: can't tell whether the amount is money in or out"]


# --- printed signs win over a DR/CR column (N7) ----------------------------------------------


def signed_csv(rows, header="Date,Description,Amount,Card Type"):
    lines = [header] + [f"{i:02d}/10/2026,{row}" for i, row in enumerate(rows, start=1)]
    return ("\n".join(lines) + "\n").encode()


def test_a_column_with_one_value_is_never_a_sign_source(ingest_env, tmp_path):
    """s7: a card-type column reads "Debit" on every row; the salary stays money in."""
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    rows = [
        "SHOP,-42.18,Debit",
        "ACME PAYROLL,900.00,Debit",
        "CAFE,-3.40,Debit",
        "WATER,-31.15,Debit",
    ]
    scripted.replies = [{"content": json.dumps(single_amount())}]
    out, doc = parse_csv(services, tmp_path, registry, "current", signed_csv(rows))
    assert [r.amount_pence for r in out.parsed.rows] == [-4218, 90000, -340, -3115]
    saved = registry.match(doc)
    assert out.errors == [] and saved is not None and saved.direction is None


def test_a_layout_learned_from_a_month_without_money_in_keeps_printed_signs(ingest_env, tmp_path):
    """s7b: the next month's salary and refund (printed positive) stay money in."""
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    scripted.replies = [{"content": json.dumps(single_amount())}]
    first = ["SHOP,-42.18,Debit", "CAFE,-3.40,Debit", "WATER,-31.15,Debit"]
    out, _ = parse_csv(services, tmp_path, registry, "current", signed_csv(first), "a.csv")
    assert out.errors == []
    later = ["SHOP,-42.18,Debit", "ACME PAYROLL,900.00,Debit", "REFUND SHOP,5.00,Debit"]
    again, _ = parse_csv(services, tmp_path, registry, "current", signed_csv(later), "b.csv")
    assert [r.amount_pence for r in again.parsed.rows] == [-4218, 90000, 500]
    assert again.errors == [] and len(scripted.requests) == 1


def test_a_cleared_flag_column_is_not_a_sign_source(ingest_env, tmp_path):
    """s6: "C" on every row is a status, not money in."""
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    rows = ["SHOP,-42.18,C", "ACME PAYROLL,900.00,C", "CAFE,-3.40,C"]
    scripted.replies = [{"content": json.dumps(single_amount())}]
    data = signed_csv(rows, header="Date,Description,Amount,Cleared")
    out, doc = parse_csv(services, tmp_path, registry, "current", data)
    assert [r.amount_pence for r in out.parsed.rows] == [-4218, 90000, -340]
    assert out.errors == [] and registry.match(doc) is not None


def test_printed_signs_win_over_an_agreeing_dr_cr_column(ingest_env, tmp_path):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    rows = ["SHOP,-42.18,DR", "ACME PAYROLL,900.00,CR", "CAFE,-3.40,DR"]
    scripted.replies = [{"content": json.dumps(single_amount(type_column="Type"))}]
    out, doc = parse_csv(services, tmp_path, registry, "current", signed_csv(rows, TYPE_HEADER))
    assert [r.amount_pence for r in out.parsed.rows] == [-4218, 90000, -340]
    saved = registry.match(doc)
    assert out.errors == [] and saved is not None and saved.direction is None


TYPE_HEADER = "Date,Description,Amount,Type"


@pytest.mark.parametrize("kind", ["current", "credit_card"])
def test_a_dr_cr_column_that_contradicts_printed_signs_is_refused(ingest_env, tmp_path, kind):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    rows = ["SHOP,-42.18,DR", "ACME PAYROLL,900.00,DR", "REFUND,5.00,CR"]
    reply = single_amount(amounts_are="money_out_negative")
    scripted.replies = [{"content": json.dumps(reply)}] * 3
    out, doc = parse_csv(services, tmp_path, registry, kind, signed_csv(rows, TYPE_HEADER))
    assert any("DR/CR column disagrees" in e for e in out.errors)
    assert registry.match(doc) is None and out.pending_layout is None
    # the printed signs are kept: nothing is turned round
    assert [r.amount_pence for r in out.parsed.rows] == [-4218, 90000, 500]


def test_a_constant_dr_column_with_unsigned_amounts_is_no_sign_source(ingest_env, tmp_path):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    rows = ["SHOP,42.18,DR", "CAFE,3.40,DR", "WATER,31.15,DR"]
    scripted.replies = [{"content": json.dumps(single_amount())}] * 3
    out, doc = parse_csv(services, tmp_path, registry, "current", signed_csv(rows, TYPE_HEADER))
    assert any("money in can't be told from money out" in e for e in out.errors)
    assert registry.match(doc) is None


def test_a_later_file_whose_printed_sign_contradicts_its_marker_is_reported():
    from tuppence.ingest.importers.csv_layout import parse_with_layout
    from tuppence.ingest.registry import CsvLayout
    from tuppence.ingest.textprep import csv_document

    layout = CsvLayout(
        id="learned-x",
        name="x",
        source="learned",
        signature=["Date", "Description", "Type", "Amount"],
        date="Date",
        description=["Description"],
        amount="Amount",
        direction="Type",
    )
    doc = csv_document(
        b"Date,Description,Type,Amount\n01/10/2026,SHOP,DR,-4.00\n02/10/2026,PAY,DR,900.00\n"
        b"03/10/2026,REFUND,CR,-5.00\n",
        sha256="x",
    )
    result = parse_with_layout(doc, layout)
    assert [r.amount_pence for r in result.parsed.rows] == [-400, -90000]
    assert result.problems == ["L4: the amount's sign and its DR/CR marker disagree"]
