"""What CSV layout learning sends, how it fails, and when a learned layout is kept."""

import json
from decimal import Decimal

from evals import oracle

from ingest.helpers import budget, use_local_model
from tuppence.ingest.mapping import HIDDEN, propose_layout
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


def test_the_oracle_answers_the_clients_json_repair_turn():
    """m7: the last user message of a repair turn has no HEADINGS; the sketch is earlier."""
    sketch = 'HEADINGS:\n["Date", "Details", "Amount"]\nROWS:\n["<DATE:dd/mm/yyyy>", "<TEXT>"]'
    answer = oracle.reply(
        [
            {"role": "system", "content": oracle.MAPPING_MARKER},
            {"role": "user", "content": sketch},
            {"role": "assistant", "content": "not json"},
            {
                "role": "user",
                "content": "That wasn't valid: x. Reply with only the corrected JSON.",
            },
        ]
    )
    assert json.loads(answer)["amount_column"] == "Amount"
