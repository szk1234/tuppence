import json

import pytest

from ingest.helpers import budget, use_local_model
from tuppence.ingest.mapping import (
    ALLOWED_DATE_FORMATS,
    MappingOut,
    mapping_to_layout,
    propose_layout,
)
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack
from tuppence.ingest.textprep import csv_document

HEADER = ["Posting Date", "Details", "Withdrawals", "Deposits", "Running Balance"]


def mapping(**changes):
    base = {
        "date_column": "Posting Date",
        "date_format": "%d/%m/%Y",
        "description_columns": ["Details"],
        "merchant_column": None,
        "amount_column": None,
        "money_out_column": "Withdrawals",
        "money_in_column": "Deposits",
        "amounts_are": "money_out_negative",
        "balance_column": "Running Balance",
        "category_column": None,
        "type_column": None,
    }
    return MappingOut.model_validate({**base, **changes})


def unknown(fixtures, name="credit-union.csv", known=None):
    data = (fixtures / "csv-unknown" / name).read_bytes()
    return csv_document(data, sha256=name, known=known)


def test_mapping_becomes_a_layout_with_the_date_format_read_here():
    sample = [["21/10/2026", "x", "1.00", "", "2.00"]]
    layout = mapping_to_layout(mapping(date_format="%m/%d/%Y"), HEADER, sample)  # ignored
    assert layout.date_formats == ["%d/%m/%Y"] and layout.money_out == "Withdrawals"
    assert layout.signature == HEADER and layout.source == "learned"
    assert "%d/%m/%Y" in ALLOWED_DATE_FORMATS


def test_bad_column_names_are_refused():
    with pytest.raises(ValueError, match="Payee"):
        mapping_to_layout(mapping(description_columns=["Payee"]), HEADER, [])


def test_unknown_csv_is_learned_once(ingest_env, fixtures):
    services, scripted = ingest_env
    use_local_model(services)
    registry = LayoutRegistry(load_bank_pack())
    doc = unknown(fixtures)
    outcome = propose_layout(doc, llm=services.llm, run=budget())
    assert outcome.errors == [] and outcome.attempts == 1
    assert outcome.result is not None and len(outcome.result.parsed.rows) == 4
    sent = scripted.requests[0]["messages"][-1]["content"]
    assert json.dumps(HEADER) in sent and sent.count("\n[") == 5  # headings + 4 rows (max 5)
    assert outcome.layout is not None
    registry.save_learned(doc, outcome.layout)
    later = unknown(fixtures, "credit-union-nov.csv", known=registry.is_known_header)
    found = registry.match(later, kind="current")  # learned for a bank account (the default)
    assert found is not None and found.id == outcome.layout.id and len(scripted.requests) == 1
    assert registry.match(later, kind="credit_card") is None  # never for a card


def test_a_mapping_that_fails_check_is_retried_then_given_up(ingest_env, fixtures):
    services, scripted = ingest_env
    use_local_model(services)
    swapped = mapping(money_out_column="Deposits", money_in_column="Withdrawals").model_dump_json()
    scripted.replies = [{"content": swapped}] * 3
    outcome = propose_layout(unknown(fixtures), llm=services.llm, run=budget())
    assert outcome.layout is None and outcome.attempts == 3 and outcome.result is not None
    assert any("Running balances don't add up" in e for e in outcome.errors)
    assert "Your previous answer didn't work" in scripted.requests[1]["messages"][-1]["content"]


def test_files_without_headings_cannot_be_learned(ingest_env):
    services, scripted = ingest_env
    doc = csv_document(b"01/10/2026,SHOP,-4.00\n02/10/2026,CAFE,-3.40\n", sha256="x")
    outcome = propose_layout(doc, llm=services.llm, run=budget())
    assert outcome.layout is None and "no row of column headings" in outcome.errors[0]
    assert scripted.requests == []
