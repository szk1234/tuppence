"""What the AI reader is sent and what it is told, including on retries."""

import datetime as dt
import re
import threading

import httpx
import pytest
from pydantic import ValidationError

from ingest.helpers import budget, use_local_model
from ingest.replies import read, reply, row
from ingest.statements import PAGE1, SECRETS, TABLE, adversarial_doc
from tuppence.ingest.identify import HeaderFacts
from tuppence.ingest.parse import ReaderLimits
from tuppence.ingest.reader import LockedBudget, read_document, tidy
from tuppence.ingest.textprep import pages_document
from tuppence.llm.budget import RunBudget
from tuppence.llm.types import LLMError

TODAY = dt.date(2026, 11, 1)


def user_text(scripted):
    return "\n".join(
        m["content"] for r in scripted.requests for m in r["messages"] if m["role"] == "user"
    )


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("window", [128_000, "local"])
def test_nothing_withheld_is_sent_on_any_path(ingest_env, retry, window):
    services, scripted = ingest_env
    local = use_local_model(services)
    if window == 128_000:  # a bigger model: the same connection, a larger window
        services.router.chain_for("read")[0][1].context_window = window
    if retry:  # a bad first answer sends the chunk again with feedback
        scripted.replies = [{"content": "not json"}, {"content": "still not json"}]
    out = read(services, adversarial_doc(), local if window == "local" else window)
    assert out.chunks >= 1
    sent = user_text(scripted)
    assert [s for s in SECRETS if s in sent] == []


def test_the_model_sees_dense_opaque_ids_only(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    doc = adversarial_doc()
    read(services, doc, rows_per_chunk=5, parallel=1)
    refs = re.findall(r"^(\w+\d+): ", user_text(scripted), re.MULTILINE)
    assert refs and all(re.fullmatch(r"D\d+", r) for r in refs)
    numbers = sorted({int(r[1:]) for r in refs})
    assert numbers == list(range(1, len(doc.data_refs) + 1))


def retry_feedback(services, scripted, doc, rows):
    scripted.requests.clear()
    scripted.replies = [{"content": reply(rows)}]
    read(services, doc, parallel=1)
    message = scripted.requests[1]["messages"][-1]["content"]
    return message


def test_feedback_is_the_same_whatever_the_withheld_lines_say(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    table = [
        TABLE,
        "01/10/2026 GREENBASKET STORES 42.18 957.82",
        "03/10/2026 LITTLE CAFE 3.40 954.42",
        "05/10/2026 ACME PAYROLL LTD 900.00 1,854.42",
    ]
    loud = pages_document(
        [[*PAGE1[:10], *table, "Account number 12345678", "Alex Example"]], sha256="x", kind="pdf"
    )
    quiet = pages_document(
        [["Example Credit Union", "Statement period 01/10/2026 to 31/10/2026", *table]],
        sha256="x",
        kind="pdf",
    )
    assert [loud.by_ref()[r].text for r in loud.data_refs[:4]] == [
        quiet.by_ref()[r].text for r in quiet.data_refs[:4]
    ]
    guesses = [
        row("D2", "42.18", sign_from="Alex Example"),  # a name in the withheld lines
        row("D3", "3.40", sign_from="12345678"),  # the account number
        row("D4", "12345678"),  # the account number as the amount text
        row("P1L7", "12345678"),  # a guessed real ref of a withheld line
        row("P1L8", "87654321"),
        row("D99", "4242", sign_from="4242"),
    ]
    assert retry_feedback(services, scripted, loud, guesses) == retry_feedback(
        services, scripted, quiet, guesses
    )


def test_a_made_up_withheld_ref_is_just_an_unknown_ref(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    message = retry_feedback(
        services, scripted, adversarial_doc(), [row("P1L7", "1.00"), row("D1", "42.18")]
    )
    assert "P1L7: source line not found" in message or "unexpected ref: P1L7" in message
    assert "Alex" not in message and "12345678" not in message


def test_a_fatal_error_stops_the_chunks_that_have_not_started(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    scripted.replies = [httpx.Response(400, json={"error": {"message": "bad"}})] * 50
    doc = adversarial_doc()
    with pytest.raises(LLMError):
        read(services, doc, rows_per_chunk=5, parallel=1)
    assert len(scripted.requests) == 1


def test_parallel_calls_reserve_their_estimate():
    inner = RunBudget(max_calls=100, max_tokens=1000, max_gbp=1.0, max_seconds=600)
    shared = LockedBudget(inner)
    first_in_flight = threading.Event()
    second_asked = threading.Event()
    answers: dict[str, str | None] = {}

    def first():
        answers["first"] = shared.over_cap(800, 0.0)
        first_in_flight.set()
        second_asked.wait(5)
        shared.start_call()
        shared.record(700, 0.0)

    def second():
        first_in_flight.wait(5)
        answers["second"] = shared.over_cap(800, 0.0)
        second_asked.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert answers["first"] is None
    assert answers["second"] and "1,000 tokens" in answers["second"]
    assert inner.tokens == 700 and inner.calls == 1


def test_a_hold_is_released_when_the_call_records_or_the_thread_finishes():
    inner = RunBudget(max_calls=100, max_tokens=1000, max_gbp=1.0, max_seconds=600)
    shared = LockedBudget(inner)
    assert shared.over_cap(800, 0.0) is None
    shared.release()
    assert shared.over_cap(800, 0.0) is None  # nothing is held any more


def test_reader_limits_are_bounded():
    for bad in ({"max_attempts_per_chunk": 0}, {"rows_per_chunk": 0}, {"parallel_chunks": 0}):
        with pytest.raises(ValidationError):
            ReaderLimits(**bad)
    with pytest.raises(ValidationError):
        ReaderLimits(max_attempts_per_chunk=4)


def test_a_non_gbp_statement_is_flagged_and_retried(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    doc = pages_document(
        [[TABLE, "01/10/2026 SHOP 1.00 99.00", "02/10/2026 CAFE 2.00 97.00"]],
        sha256="x",
        kind="pdf",
    )
    bad = reply(
        [row("D2", "1.00", running=99.0), row("D3", "2.00", running=97.0)],
        [{"ref": "D1", "reason": "headings"}],
        "USD",
    )
    scripted.replies = [{"content": bad}] * 3
    out = read(services, doc)
    assert not out.ok and any("not GBP" in e for e in out.errors)


def test_model_text_in_errors_is_capped_and_cleaned(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    doc = pages_document(
        [[TABLE, "01/10/2026 SHOP 1.00 99.00", "02/10/2026 CAFE 2.00 97.00"]],
        sha256="x",
        kind="pdf",
    )
    nasty = "Paid\x1b[31m out " + "x" * 5000
    rows = [row("D2", "1.00", sign_from=nasty), row("D3", "2.00", sign_from=nasty)]
    scripted.replies = [{"content": reply(rows, [{"ref": "D1", "reason": "headings"}])}] * 3
    out = read(services, doc)
    assert out.errors
    assert all(len(e) <= 200 and "\x1b" not in e for e in out.errors)
    assert tidy("a\x00b" * 100).count("\x00") == 0


def test_screenshots_withhold_names_and_account_numbers(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    rows = [
        "Alex Example",
        "Current account",
        "Sort code 12-34-56 Account 12345678",
        "Balance £1,234.56",
        "5 Oct GREENBASKET STORES -£3.40",
        "6 Oct LITTLE CAFE -£12.80",
    ]
    doc = pages_document([rows], sha256="x", kind="image")
    out = read_document(
        doc,
        llm=services.llm,
        run=budget(),
        perspective="household",
        level="screenshot",
        account="current account",
        facts=HeaderFacts(),
        today=dt.date(2026, 10, 9),
        context_window=128_000,
    )
    sent = user_text(scripted)
    # The app's balance line is the first line with an amount, so it is read like a row (and
    # skipped as a balance); only the lines above it, and account details, are held back.
    assert [s for s in ("Alex Example", "12-34-56", "12345678") if s in sent] == []
    assert "GREENBASKET" in sent and len(out.parsed.rows) == 2
