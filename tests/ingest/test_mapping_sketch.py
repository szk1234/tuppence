"""CSV layout learning sends a sketch of the file's shape, never a value from it.

The model sees the column headings (any heading naming account details hidden, long digit runs
replaced) and one type token per sample cell. Every value below is invented.
"""

import json
import re

import pytest

from ingest.helpers import budget, use_local_model
from tuppence.ingest.mapping import (
    HIDDEN,
    cell_token,
    header_problem,
    mapping_to_layout,
    propose_layout,
    shown_heading,
)
from tuppence.ingest.textprep import csv_document


def user_messages(scripted):
    return [m["content"] for r in scripted.requests for m in r["messages"] if m["role"] == "user"]


@pytest.mark.parametrize(
    ("cell", "token"),
    [
        ("", "<EMPTY>"),
        ("   ", "<EMPTY>"),
        ("21/10/2026", "<DATE:dd/mm/yyyy>"),
        ("2026-10-21", "<DATE:yyyy-mm-dd>"),
        ("21 Oct 2026", "<DATE:dd mon yyyy>"),
        ("10/25/2026", "<DATE:mm/dd/yyyy>"),
        ("1234.56", "<AMOUNT:12.30>"),
        ("1,234.56", "<AMOUNT:12.30>"),
        ("-12.30", "<AMOUNT:-12.30>"),
        ("(12.30)", "<AMOUNT:(12.30)>"),
        ("12.30 DR", "<AMOUNT:12.30 DR>"),
        ("12.30CR", "<AMOUNT:12.30 CR>"),
        ("+£250.00", "<AMOUNT:+£12.30>"),
        ("12.30-", "<AMOUNT:12.30->"),
        ("12345678", "<NUMBER:8 digits>"),
        ("12-34-56", "<HIDDEN>"),  # a sort code: hidden outright
        ("404784", "<NUMBER:6 digits>"),
        ("GREENBASKET STORES 0873", "<TEXT>"),
        ("Alex Example", "<TEXT>"),
    ],
)
def test_each_cell_becomes_a_type_token(cell, token):
    assert cell_token(cell) == token


# Every identifier format the re-review found getting through a value-shape mask.
IDENTIFIERS = {
    "dashed sort code": "12-34-56",
    "dashless sort code": "404784",
    "dashless sort code 2": "123456",
    "spaced sort code": "12 34 56",
    "dotted sort code": "12.34.56",
    "en-dash sort code": "12–34–56",
    "sort code with apostrophe": "'30-12-34",
    "8-digit account": "12345678",
    "account that parses as yyyymmdd": "12341225",
    "account that parses as yyyymmdd 2": "20261001",
    "7-digit reference that parses as yyyymmdd": "2026101",
    "spaced account": "1234 5678",
    "xlsx float account": "12345678.00",
    "signed account": "-12345678",
    "short account reference": "-71004",
    "short account reference bare": "71004",
    "bare card last 4": "4242",
    "masked card": "************4242",
    "full card": "4929123456789012",
    "spaced card": "4929 1234 5678 9012",
    "iban": "GB29 NWBK 6016 1331 9268 19",
    "full-width account": "１２３４５６７８",
    "phone": "+447700900123",
    "holder untitled": "Alex Example",
    "holder upper": "ALEX EXAMPLE",
    "holder initial": "A EXAMPLE",
    "holder titled": "Mr Alex Example",
    "postcode": "EX1 2MP",
    "description with own name": "TRANSFER FROM ALEX EXAMPLE",
    "description with a reference": "FPO PAT EXAMPLE REF 71004",
}
HEADER = "Date,Description,Reference,Card Member,Sort Code,Account Number,Amount,Balance"


def identifier_csv(value: str) -> bytes:
    cell = '"' + value.replace('"', '""') + '"'
    rows = [HEADER]
    for i in range(1, 8):
        rows.append(f"0{i}/10/2026,{cell},{cell},{cell},{cell},{cell},-1{i}.00,{900 - i}.00")
    return ("\n".join(rows) + "\n").encode()


def mapping(**changes):
    base = {
        "date_column": "Date",
        "description_columns": ["Description"],
        "merchant_column": None,
        "amount_column": "Amount",
        "money_out_column": None,
        "money_in_column": None,
        "amounts_are": "money_out_negative",
        "balance_column": "Balance",
        "category_column": None,
        "type_column": None,
    }
    return {**base, **changes}


@pytest.mark.parametrize("name", list(IDENTIFIERS))
def test_no_cell_value_reaches_the_model_on_any_attempt(ingest_env, name):
    services, scripted = ingest_env
    use_local_model(services)
    value = IDENTIFIERS[name]
    scripted.replies = [
        {"content": json.dumps(mapping(amount_column="Reference"))},  # reads ids as amounts
        {"content": json.dumps(mapping(date_column="Account Number"))},
        {"content": json.dumps(mapping(description_columns=["Card Member"]))},
    ]
    doc = csv_document(identifier_csv(value), sha256="x")
    propose_layout(doc, llm=services.llm, run=budget())
    assert len(scripted.requests) == 3
    sent = "\n".join(user_messages(scripted))
    assert value not in sent
    for run in re.findall(r"\d{4,}", value):  # nor any long digit run inside it
        assert run not in sent
    assert "ALEX" not in sent.upper() and "EXAMPLE" not in sent.upper()


def test_the_sketch_shows_headings_and_tokens_only(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    scripted.replies = [{"content": json.dumps(mapping())}]
    propose_layout(
        csv_document(identifier_csv("12345678"), sha256="x"), llm=services.llm, run=budget()
    )
    sent = user_messages(scripted)[0]
    headings = json.loads(sent.split("HEADINGS:\n")[1].splitlines()[0])
    assert headings == [
        "Date",
        "Description",
        "Reference",
        "Card Member",
        HIDDEN,
        HIDDEN,
        "Amount",
        "Balance",
    ]
    first = json.loads(sent.split("ROWS:\n")[1].splitlines()[0])
    assert first == [
        "<DATE:dd/mm/yyyy>",
        "<NUMBER:8 digits>",
        "<NUMBER:8 digits>",
        "<NUMBER:8 digits>",
        "<NUMBER:8 digits>",
        "<NUMBER:8 digits>",
        "<AMOUNT:-12.30>",
        "<AMOUNT:12.30>",
    ]


def test_headings_are_masked_and_mapped_back():
    assert shown_heading("Sort Code") == HIDDEN
    assert shown_heading("Account Number") == HIDDEN
    assert shown_heading("Card Number") == HIDDEN
    assert shown_heading("Ref 12345") == "Ref <NUM>"
    assert shown_heading("Paid out") == "Paid out"
    header = ["Date", "Ref 12345", "Account Number", "Amount"]
    rows = [["01/10/2026", "x", "y", "-1.00"]]
    layout = mapping_to_layout(
        _out(mapping(description_columns=["Ref <NUM>"], balance_column=None)), header, rows
    )
    assert layout.description == ["Ref 12345"]  # the file's own heading, for the importer
    with pytest.raises(ValueError, match="isn't in the header"):
        mapping_to_layout(
            _out(mapping(description_columns=["Account Number"], balance_column=None)),
            header,
            rows,
        )


def _out(data):
    from tuppence.ingest.mapping import MappingOut

    return MappingOut.model_validate(data)


@pytest.mark.parametrize(
    "header",
    [
        ["Name", "Alex Example"],  # key/value preamble
        ["Date", "Amount"],  # too few columns to tell
        ["Account 12345678 (Alex Example)", "Date", "Description", "Amount"],
        ["Date", "Description", "Amount", "Mr Alex Example"],
        ["Date", "Description", "Amount", "Sort code 12-34-56"],
        ["Statement date:", "Account name:", "Period:"],
        ["Example Credit Union", "Current Account", "October 2026"],
    ],
)
def test_a_row_that_is_not_a_real_header_is_refused(header):
    assert header_problem(header) is not None


def test_ordinary_headers_are_accepted():
    for header in (
        ["Date", "Description", "Amount", "Balance"],
        ["Transaction Date", "Type", "Sort Code", "Account Number", "Description", "Debit Amount"],
        ["Posting Date", "Details", "Withdrawals", "Deposits", "Running Balance"],
    ):
        assert header_problem(header) is None


def test_a_key_value_preamble_never_reaches_the_model(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    data = (
        b"Name,Alex Example\n"
        b"Statement date,01/10/2026\n"
        b"Date,Description,Amount,Balance\n"
        b"01/10/2026,SHOP,-1.00,99.00\n"
        b"02/10/2026,CAFE,-2.00,97.00\n"
    )
    outcome = propose_layout(csv_document(data, sha256="x"), llm=services.llm, run=budget())
    assert outcome.layout is None and scripted.requests == []
    assert outcome.errors and "column headings" in outcome.errors[0]


def test_a_header_holding_account_details_never_reaches_the_model(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    data = (
        b"Date,Description,Amount,Balance,Account 12345678 (Alex Example)\n"
        b"01/10/2026,SHOP,-1.00,99.00,\n"
        b"02/10/2026,CAFE,-2.00,97.00,\n"
    )
    outcome = propose_layout(csv_document(data, sha256="x"), llm=services.llm, run=budget())
    assert outcome.layout is None and scripted.requests == []
    assert "account details" in outcome.errors[0]


def long_digit_csv(n=25):
    rows = ["Date,Description,Reference,Card Number,Amount,Balance"]
    balance = 1000
    for i in range(1, n + 1):
        balance -= 10 + i
        ref, card = f"0770090{i:04d}", f"49291234567890{i:02d}"
        rows.append(f"{i:02d}/10/2026,SHOP {i},{ref},{card},-{10 + i}.00,{balance}.00")
    return ("\n".join(rows) + "\n").encode()


@pytest.mark.parametrize("column", ["Reference", "Description", "Balance"])
def test_retry_feedback_counts_problems_and_names_columns_only(ingest_env, column):
    services, scripted = ingest_env
    use_local_model(services)
    scripted.replies = [
        {"content": json.dumps(mapping(amount_column=column))},
        {"content": json.dumps(mapping())},
    ]
    outcome = propose_layout(
        csv_document(long_digit_csv(), sha256="x"), llm=services.llm, run=budget()
    )
    assert outcome.layout is not None and outcome.attempts == 2
    feedback = user_messages(scripted)[1].split("Your previous answer didn't work:")[1]
    assert re.search(r"\d{3,}", feedback) is None  # counts only: no figure, id or row ref
    assert "SHOP" not in feedback and "4929" not in feedback
    assert re.search(r"\(\d+ rows?\)|on \d+ rows?", feedback)


def test_the_date_format_is_worked_out_on_this_device():
    header = ["Date", "Description", "Amount"]
    iso = [["2026-10-01", "x", "-1.00"], ["2026-10-21", "y", "-2.00"]]
    us = [["10/01/2026", "x", "-1.00"], ["10/25/2026", "y", "-2.00"]]
    uk = [["01/10/2026", "x", "-1.00"], ["02/10/2026", "y", "-2.00"]]
    out = _out(mapping(balance_column=None))
    assert mapping_to_layout(out, header, iso).date_formats == ["%Y-%m-%d"]
    assert mapping_to_layout(out, header, us).date_formats == ["%m/%d/%Y"]
    assert mapping_to_layout(out, header, uk).date_formats == ["%d/%m/%Y"]  # UK first
    with pytest.raises(ValueError, match="'Description' \\(date_column\\)"):
        mapping_to_layout(_out(mapping(date_column="Description", balance_column=None)), header, uk)
