import datetime as dt
import json

import pytest
from evals import oracle

from ingest.helpers import budget, use_local_model
from tuppence.ingest.check import check_statement
from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.identify import HeaderFacts, header_facts
from tuppence.ingest.reader import (
    ReadOut,
    context_block,
    read_document,
    retry_message,
    rows_per_chunk_for,
    to_parsed,
    user_message,
)
from tuppence.ingest.textprep import plan_chunks
from tuppence.llm.types import BudgetExceeded

LIMITS = ExtractLimits()
TODAY = dt.date(2026, 11, 1)
CARD = "credit card (the statement prints purchases as positive figures)"
EMPTY = json.dumps(
    {
        "statement": {
            "period_start": None,
            "period_end": None,
            "opening_balance": None,
            "closing_balance": None,
            "currency": "GBP",
        },
        "transactions": [],
        "skipped": [],
    }
)


def card_doc(fixtures):
    return extract_document(fixtures / "pdf" / "card-text.pdf", "pdf", sha256="x", limits=LIMITS)


def facts_of(doc):
    by_ref = doc.by_ref()
    return header_facts("\n".join(by_ref[r].text for r in doc.preamble_refs))


def test_rows_per_chunk_fits_the_context_window():
    assert rows_per_chunk_for(4096, 40) == 16
    assert rows_per_chunk_for(128_000, 40) == 40
    assert rows_per_chunk_for(2048, 40) == 5


def test_card_pdf_reads_cleanly_and_balances(ingest_env, fixtures):
    services, scripted = ingest_env
    window = use_local_model(services)
    doc = card_doc(fixtures)
    out = read_document(
        doc,
        llm=services.llm,
        run=budget(),
        perspective="card",
        level="full",
        account=CARD,
        facts=facts_of(doc),
        today=TODAY,
        context_window=window,
    )
    assert out.ok and out.errors == [] and out.attempts == 1
    assert out.chunks == len(scripted.requests) and len(out.parsed.rows) == 13
    out.parsed.opening_balance_pence, out.parsed.closing_balance_pence = 84216, 90985
    assert check_statement(out.parsed, dates=True) == []


def test_the_preamble_never_leaves_the_device(ingest_env, fixtures):
    services, scripted = ingest_env
    window = use_local_model(services)
    doc = card_doc(fixtures)
    read_document(
        doc,
        llm=services.llm,
        run=budget(),
        perspective="card",
        level="full",
        account=CARD,
        facts=facts_of(doc),
        today=TODAY,
        context_window=window,
    )
    sent = json.dumps(scripted.requests)
    assert "Alex Example" not in sent and "1 Example Road" not in sent and "EX1 2MP" not in sent
    assert "STATEMENT HEADER: period 2026-09-29 to 2026-10-28\\n" in sent
    # the summary box is withheld too: no balance figure or total reaches the model
    for figure in ("842.16", "909.85", "259.04", "190.00", "3,000.00", "25.00", "4242"):
        assert figure not in sent
    for ref in doc.preamble_refs:
        assert doc.by_ref()[ref].text not in sent


def test_a_wrong_reply_is_sent_back_with_the_errors(ingest_env, fixtures):
    services, scripted = ingest_env
    use_local_model(services)
    doc = extract_document(fixtures / "pdf" / "current-text.pdf", "pdf", sha256="x", limits=LIMITS)
    context = context_block(
        today=TODAY, account="current account", facts=facts_of(doc), level="full"
    )
    first = oracle.read(user_message(context, plan_chunks(doc, rows_per_chunk=40)[0], doc))
    first["transactions"][0]["amount"] = -42.81  # a misread digit
    scripted.replies = [{"content": json.dumps(first)}]
    out = read_document(
        doc,
        llm=services.llm,
        run=budget(),
        perspective="household",
        level="full",
        account="current account",
        facts=facts_of(doc),
        today=TODAY,
        context_window=128_000,
    )
    assert out.ok and out.attempts == 2
    retry = scripted.requests[1]["messages"][-1]["content"]
    assert "Your previous answer failed verification:" in retry
    assert 'amount_text "42.18" is 42.18, amount is -42.81' in retry


def test_three_bad_attempts_leave_a_draft_and_the_errors(ingest_env, fixtures):
    services, scripted = ingest_env
    use_local_model(services)
    # attempt 1: not JSON, and not JSON again after the client's one repair call;
    # attempts 2 and 3: valid JSON that leaves every line out.
    scripted.replies = [
        {"content": "nope"},
        {"content": "still nope"},
        {"content": EMPTY},
        {"content": EMPTY},
    ]
    out = read_document(
        card_doc(fixtures),
        llm=services.llm,
        run=budget(),
        perspective="card",
        level="full",
        account=CARD,
        facts=HeaderFacts(),
        today=TODAY,
        context_window=128_000,
        rows_per_chunk=100,
    )
    assert not out.ok and out.attempts == 3 and len(scripted.requests) == 4
    assert any(e.startswith("missing refs:") for e in out.errors)


def test_the_run_budget_stops_the_read(ingest_env, fixtures):
    services, _ = ingest_env
    window = use_local_model(services)
    with pytest.raises(BudgetExceeded):
        read_document(
            card_doc(fixtures),
            llm=services.llm,
            run=budget(calls=1),
            perspective="card",
            level="full",
            account=CARD,
            facts=HeaderFacts(),
            today=TODAY,
            context_window=window,
            rows_per_chunk=5,
            parallel=1,
        )


def test_retry_message_fits_small_models():
    errors = [
        f"L{i}: running balance mismatch (previous 1000.00 + amount -42.18 = 957.82, got 957.00)"
        for i in range(1, 41)
    ]
    small = retry_message("FILE:\nL1: x", errors, "x" * 20_000, context_window=4096)
    assert "L1:" in small and "L3:" in small and "L40:" not in small and "xxxx" not in small
    assert len(small) <= int(4096 * 0.6 - 1700) * 4
    big = retry_message("FILE:\nL1: x", errors, '{"previous": 1}', context_window=128_000)
    assert "L40:" in big and big.endswith('{"previous": 1}')


def test_conversion_errors_are_reported():
    row = {
        "ref": "P1L2",
        "date": "yesterday",
        "amount": -1.0,
        "amount_text": "1.00",
        "sign_from": None,
        "raw_desc": "x",
        "merchant": None,
        "bank_category": None,
        "bank_type": None,
        "running_balance": None,
    }
    out = ReadOut.model_validate(
        {
            "statement": {
                "period_start": "29/09/2026",
                "period_end": "2026-10-28",
                "opening_balance": 842.16,
                "closing_balance": None,
                "currency": None,
            },
            "transactions": [row],
            "skipped": [],
        }
    )
    parsed, errors = to_parsed(out, perspective="household")
    assert errors == ["statement period_start 29/09/2026 not ISO", "P1L2: date yesterday not ISO"]
    assert parsed.opening_balance_pence == 84216 and parsed.rows == []


def test_screenshot_reads_without_period_or_preamble(ingest_env, fixtures):
    services, scripted = ingest_env
    window = use_local_model(services)
    shot = fixtures / "image" / "app-screenshot.png"
    doc = extract_document(shot, "image", sha256="x", limits=LIMITS)
    out = read_document(
        doc,
        llm=services.llm,
        run=budget(),
        perspective="household",
        level="screenshot",
        account="current account",
        facts=HeaderFacts(),
        today=dt.date(2026, 10, 9),
        context_window=window,
    )
    assert out.ok
    assert [(r.date.isoformat(), r.amount_pence) for r in out.parsed.rows] == [
        ("2026-10-05", -340),
        ("2026-10-05", -2460),
        ("2026-10-06", -1280),
        ("2026-10-07", 25000),
        ("2026-10-08", -799),
    ]
    assert (
        "This is a screenshot from a banking app."
        in scripted.requests[0]["messages"][-1]["content"]
    )


def test_parallel_chunks_share_one_budget(ingest_env, fixtures):
    services, scripted = ingest_env
    use_local_model(services)
    run = budget()
    doc = card_doc(fixtures)
    out = read_document(
        doc,
        llm=services.llm,
        run=run,
        perspective="card",
        level="full",
        account=CARD,
        facts=facts_of(doc),
        today=TODAY,
        context_window=128_000,
        rows_per_chunk=5,
        parallel=2,
    )
    assert out.ok and out.chunks >= 3
    assert run.calls == len(scripted.requests) == out.chunks


def test_no_withheld_line_is_ever_sent(ingest_env, fixtures):
    services, scripted = ingest_env
    window = use_local_model(services)
    for name in ("card-text", "current-text"):
        doc = extract_document(fixtures / "pdf" / f"{name}.pdf", "pdf", sha256="x", limits=LIMITS)
        read_document(
            doc,
            llm=services.llm,
            run=budget(),
            perspective="household",
            level="full",
            account="current account",
            facts=facts_of(doc),
            today=TODAY,
            context_window=window,
        )
        sent = "\n".join(m["content"] for r in scripted.requests for m in r["messages"])
        withheld = [doc.by_ref()[r].text for r in doc.preamble_refs]
        assert withheld and not [t for t in withheld if t in sent]
        scripted.requests.clear()


def test_nan_and_absurd_replies_are_retried_not_raised(ingest_env, fixtures):
    services, scripted = ingest_env
    use_local_model(services)
    doc = extract_document(fixtures / "pdf" / "current-text.pdf", "pdf", sha256="x", limits=LIMITS)
    context = context_block(
        today=TODAY, account="current account", facts=facts_of(doc), level="full"
    )
    good = oracle.read(user_message(context, plan_chunks(doc, rows_per_chunk=40)[0], doc))
    text = json.dumps(good).replace('"amount": -42.18', '"amount": NaN', 1)
    huge = json.dumps(good).replace('"amount": -42.18', '"amount": 1e300', 1)
    assert "NaN" in text and "1e300" in huge
    scripted.replies = [{"content": text}, {"content": text}, {"content": huge}]
    out = read_document(
        doc,
        llm=services.llm,
        run=budget(),
        perspective="household",
        level="full",
        account="current account",
        facts=facts_of(doc),
        today=TODAY,
        context_window=128_000,
    )
    assert out.ok and out.attempts >= 2
