import datetime as dt

import pytest

from ingest.helpers import budget, use_local_model
from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.identify import identify
from tuppence.ingest.parse import ReaderLimits, parse_document
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack
from tuppence.llm.types import NoModelConfigured

PACK = load_bank_pack()


def run_parse(services, fixtures, relative, kind, account_kind, *, registry=None, window=None):
    registry = registry or LayoutRegistry(PACK)
    path = fixtures / relative
    doc = extract_document(
        path, kind, sha256="x", limits=ExtractLimits(), known_header=registry.is_known_header
    )
    evidence = identify(doc, pack=PACK, registry=registry, key=b"test-key")
    outcome = parse_document(
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
    if outcome.learned_layout is not None:  # what the import does, in its own transaction
        registry.save_learned(doc, outcome.learned_layout)
    return outcome


def test_known_csv_and_ofx_need_no_ai(ingest_env, fixtures):
    services, scripted = ingest_env
    monzo = run_parse(services, fixtures, "csv/monzo.csv", "csv", "current")
    ofx = run_parse(services, fixtures, "ofx/current.ofx", "ofx", "current")
    assert (monzo.errors, monzo.info["importer"], len(monzo.parsed.rows)) == ([], "csv:monzo", 9)
    assert (ofx.errors, ofx.info["importer"]) == ([], "ofx")
    assert scripted.requests == []


def test_unknown_csv_is_mapped_once_and_saved(ingest_env, fixtures):
    services, scripted = ingest_env
    window = use_local_model(services)
    registry = LayoutRegistry(PACK)
    first = run_parse(
        services,
        fixtures,
        "csv-unknown/credit-union.csv",
        "csv",
        "current",
        registry=registry,
        window=window,
    )
    again = run_parse(
        services,
        fixtures,
        "csv-unknown/credit-union-nov.csv",
        "csv",
        "current",
        registry=registry,
        window=window,
    )
    assert first.errors == [] and first.info["importer"].startswith("csv:learned-")
    assert again.errors == [] and again.info["importer"] == first.info["importer"]
    assert len(scripted.requests) == 1


def test_pdf_uses_the_header_read_on_this_device(ingest_env, fixtures):
    services, _ = ingest_env
    window = use_local_model(services)
    out = run_parse(services, fixtures, "pdf/card-text.pdf", "pdf", "credit_card", window=window)
    assert out.errors == [] and out.level == "full" and out.info["importer"] == "ai-read"
    parsed = out.parsed
    assert (parsed.period_start, parsed.period_end) == (dt.date(2026, 9, 29), dt.date(2026, 10, 28))
    assert (parsed.opening_balance_pence, parsed.closing_balance_pence) == (84216, 90985)
    assert parsed.perspective == "card"


def test_screenshot_gets_the_screenshot_checks(ingest_env, fixtures):
    services, _ = ingest_env
    window = use_local_model(services)
    out = run_parse(
        services, fixtures, "image/app-screenshot.png", "image", "current", window=window
    )
    assert out.errors == [] and out.level == "screenshot" and len(out.parsed.rows) == 5


def test_a_pdf_without_an_ai_model_says_so(ingest_env, fixtures):
    services, _ = ingest_env
    with pytest.raises(NoModelConfigured):
        run_parse(services, fixtures, "pdf/current-text.pdf", "pdf", "current")
