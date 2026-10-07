"""What CSV layout learning sends, how it fails, and when a learned layout is kept."""

import datetime as dt
import json
from decimal import Decimal

import pytest
from evals import oracle

from ingest.helpers import budget, use_local_model
from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.identify import identify
from tuppence.ingest.mapping import HIDDEN, propose_layout
from tuppence.ingest.parse import ReaderLimits, parse_document
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack
from tuppence.ingest.textprep import csv_document

IDENTIFIERS = ["12345678", "12-34-56", "87654321", "20-00-00", "4929 1234 5678 9012"]


def build_csv(n=25):
    rows = [
        "Example Credit Union export for Alex Example",
        "Account 12345678 Sort code 12-34-56",
        "Transaction Date,Type,Sort Code,Account Number,Description,Paid Out,Paid In,Balance",
    ]
    balance = Decimal("1000.00")
    for i in range(1, n + 1):
        out = Decimal("10.00") + i
        balance -= out
        desc = f"SHOP NUMBER {i}"
        if i == 3:
            desc = "FPO TO PAT EXAMPLE 20-00-00 87654321"
        if i == 14:
            desc = "CARD 4929 1234 5678 9012 HOMEWARE"
        rows.append(f"{i:02d}/10/2026,DEB,12-34-56,12345678,{desc},{out},,{balance}")
    return ("\n".join(rows) + "\n").encode()


def user_messages(scripted):
    return [m["content"] for r in scripted.requests for m in r["messages"] if m["role"] == "user"]


def good_mapping(**changes):
    base = {
        "date_column": "Transaction Date",
        "date_format": "%d/%m/%Y",
        "description_columns": ["Description"],
        "merchant_column": None,
        "amount_column": None,
        "money_out_column": "Paid Out",
        "money_in_column": "Paid In",
        "amounts_are": "money_out_negative",
        "balance_column": "Balance",
        "category_column": None,
        "type_column": None,
    }
    return {**base, **changes}


def test_no_identifier_is_sent_on_the_first_try_or_on_retries(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    wrong = good_mapping(amount_column="Description", money_out_column=None, money_in_column=None)
    scripted.replies = [{"content": json.dumps(wrong)}, {"content": json.dumps(good_mapping())}]
    outcome = propose_layout(csv_document(build_csv(), sha256="x"), llm=services.llm, run=budget())
    assert outcome.layout is not None and outcome.attempts == 2
    sent = "\n".join(user_messages(scripted))
    assert [i for i in IDENTIFIERS if i in sent] == []
    assert "Alex Example" not in sent
    assert HIDDEN in sent and "<NUMBER:8 digits>" in sent  # the shape is still visible


def test_retries_never_quote_cell_values(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    wrong = good_mapping(amount_column="Description", money_out_column=None, money_in_column=None)
    scripted.replies = [{"content": json.dumps(wrong)}, {"content": json.dumps(good_mapping())}]
    propose_layout(csv_document(build_csv(), sha256="x"), llm=services.llm, run=budget())
    feedback = user_messages(scripted)[1].split("Your previous answer didn't work:")[1]
    for i in range(6, 26):  # rows beyond the five samples
        assert f"SHOP NUMBER {i}" not in feedback
    assert "HOMEWARE" not in feedback and "PAT EXAMPLE" not in feedback
    assert "Amounts in column 'Description' couldn't be read" in feedback


def test_bad_columns_get_specific_messages_the_model_and_the_user_see(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    bad = good_mapping(description_columns=["Payee"])
    scripted.replies = [{"content": json.dumps(bad)}] * 3
    outcome = propose_layout(csv_document(build_csv(), sha256="x"), llm=services.llm, run=budget())
    assert outcome.layout is None
    assert outcome.errors == ["Column 'Payee' (description_columns) isn't in the header"]
    assert (
        "Column 'Payee' (description_columns) isn't in the header"
        in user_messages(scripted)[1].split("didn't work:")[1]
    )


def test_a_date_format_that_does_not_fit_is_named_without_values(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    bad = good_mapping(date_column="Type", date_format="%d/%m/%Y")
    scripted.replies = [{"content": json.dumps(bad)}] * 3
    outcome = propose_layout(csv_document(build_csv(), sha256="x"), llm=services.llm, run=budget())
    assert outcome.errors == [
        "Column 'Type' (date_column) doesn't hold dates in a format Tuppence can read"
    ]


def test_an_incomplete_amount_mapping_says_what_is_missing(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    bad = good_mapping(money_in_column=None)
    scripted.replies = [{"content": json.dumps(bad)}] * 3
    outcome = propose_layout(csv_document(build_csv(), sha256="x"), llm=services.llm, run=budget())
    assert outcome.errors == [
        "Give either amount_column, or both money_out_column and money_in_column"
    ]


def test_the_oracle_answers_a_mapping_retry():
    user = (
        'HEADINGS:\n["Date", "Details", "<HIDDEN>", "Amount"]\n'
        'ROWS:\n["<DATE:dd/mm/yyyy>", "<TEXT>", "<NUMBER:8 digits>", "<AMOUNT:-12.30>"]\n\n'
        "Your previous answer didn't work:\n"
        "Dates in column 'Date' couldn't be read as dd/mm/yyyy on 2 rows\n{}"
    )
    answer = oracle.reply(
        [
            {"role": "system", "content": oracle.MAPPING_MARKER},
            {"role": "user", "content": user},
        ]
    )
    assert json.loads(answer)["date_column"] == "Date"


CURRENT = (
    b"Date,Narrative,Amount,Balance\n"
    b"01/10/2026,GREENBASKET STORES,-42.18,957.82\n"
    b"03/10/2026,LITTLE CAFE,-3.40,954.42\n"
    b"05/10/2026,ACME PAYROLL LTD,900.00,1854.42\n"
    b"10/10/2026,CITY WATER,-31.15,1823.27\n"
    b"12/10/2026,HOMEWARE DIRECT,-43.27,1780.00\n"
)
INVERTED = {
    "date_column": "Date",
    "date_format": "%d/%m/%Y",
    "description_columns": ["Narrative"],
    "merchant_column": None,
    "amount_column": "Amount",
    "money_out_column": None,
    "money_in_column": None,
    "amounts_are": "purchases_positive",
    "balance_column": "Balance",
    "category_column": None,
    "type_column": None,
}


def parse_csv(services, tmp_path, registry, account_kind, data=CURRENT):
    window = services.router.chain_for("read")[0][1].context_window
    path = tmp_path / "export.csv"
    path.write_bytes(data)
    pack = load_bank_pack()
    doc = extract_document(
        path, "csv", sha256="x", limits=ExtractLimits(), known_header=registry.is_known_header
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


def test_a_back_to_front_mapping_is_flagged_and_not_kept(ingest_env, tmp_path):
    services, scripted = ingest_env
    registry = LayoutRegistry(load_bank_pack())
    use_local_model(services)
    scripted.replies = [{"content": json.dumps(INVERTED)}]
    out, doc = parse_csv(services, tmp_path, registry, "current")
    # the running balance adds up either way round, so only the account type can tell
    assert [r.amount_pence for r in out.parsed.rows][:2] == [4218, 340]
    assert any("money in" in e and "back to front" in e for e in out.errors)
    assert registry.match(doc) is None  # not saved
    assert out.pending_layout is not None and out.pending_layout.perspective == "card"


def test_a_right_way_round_mapping_is_kept(ingest_env, tmp_path):
    services, scripted = ingest_env
    registry = LayoutRegistry(load_bank_pack())
    use_local_model(services)
    right = dict(INVERTED, amounts_are="money_out_negative")
    scripted.replies = [{"content": json.dumps(right)}]
    out, doc = parse_csv(services, tmp_path, registry, "current")
    assert out.errors == [] and out.pending_layout is None
    assert registry.match(doc) is not None
    assert [r.amount_pence for r in out.parsed.rows][:2] == [-4218, -340]


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
        **INVERTED,
        "amount_column": None,
        "money_out_column": "Money in",
        "money_in_column": "Money out",
        "amounts_are": "money_out_negative",
        "balance_column": None,
        "description_columns": ["Details"],
    }
    scripted.replies = [{"content": json.dumps(swapped)}] * 3
    out, doc = parse_csv(services, tmp_path, registry, "current", data)
    # the column headings contradict the signs, so Check refuses it and nothing is kept
    assert any("wrong way round" in e for e in out.errors) and registry.match(doc) is None


@pytest.mark.parametrize("kind", ["savings"])
def test_other_account_types_are_not_second_guessed(ingest_env, tmp_path, kind):
    services, scripted = ingest_env
    registry = LayoutRegistry(load_bank_pack())
    use_local_model(services)
    scripted.replies = [{"content": json.dumps(INVERTED)}]
    out, _ = parse_csv(services, tmp_path, registry, kind)
    assert out.pending_layout is None
