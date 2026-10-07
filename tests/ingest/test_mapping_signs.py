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
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    header = "Date,Narrative,Type,Amount,Balance"
    data = (
        f"{header}\n01/10/2026,GREENBASKET STORES,DEBIT,42.18,142.18\n"
        "02/10/2026,LITTLE CAFE,DEBIT,3.40,145.58\n"
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
