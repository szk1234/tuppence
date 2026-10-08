"""Regression tests from the Task 9 review (R-M3-19): rows that overlapping statements share,
answers that survive a restart, no statement text left on disk, account types, the fix-up
screen's limits and the start-up sweep. All statements are synthetic."""

import datetime as dt
import json

import pytest
from pydantic import ValidationError

from ingest.helpers import (
    add_account,
    cloud_model,
    drain,
    stored_rows,
    threads,
    upload,
    use_local_model,
)
from make_statement_fixtures import private_two_page_pdf
from tuppence.app.services import build_services
from tuppence.core.errors import InputError
from tuppence.core.money import MAX_PENCE
from tuppence.core.records import VersionConflict
from tuppence.core.secrets import SecretUnreadable
from tuppence.ingest import pipeline
from tuppence.ingest.handoff import ANALYSIS_JOB
from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement, SkippedLine
from tuppence.ingest.service import IngestService, RowEdit
from tuppence.settings import RuntimeSettings


def restart(tmp_path):
    return build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))


# --- I1: statements that cover the same rows --------------------------------------------------


def chase_twice(services, fixtures):
    """The Chase CSV, then the same month again with one description reworded."""
    chase = add_account(services, "chase", "current", "Chase")
    first = upload(services, fixtures, "csv/chase.csv")
    drain(services)
    reworded = (
        (fixtures / "csv" / "chase.csv")
        .read_text()
        .replace("Greenbasket Stores", "GREENBASKET STORES 0873 LONDON")
    )
    second = services.ingest.upload("chase-again.csv", reworded.encode())
    drain(services)
    return chase, first.record.id, second.record.id


def test_each_statement_lists_every_row_it_covers(ingest_env, fixtures):
    services, _ = ingest_env
    chase, first, second = chase_twice(services, fixtures)
    assert stored_rows(services, chase.id) == 9
    rows = services.statements.transactions(second)
    assert len(rows) == 9
    assert sorted(t.match for t in rows) == ["exact"] * 8 + ["similar"]
    assert {t.id for t in rows} == {t.id for t in services.statements.transactions(first)}


@pytest.mark.parametrize("removed", ["first", "second"])
def test_removing_one_of_two_overlapping_statements_keeps_the_others_rows(
    ingest_env, fixtures, removed
):
    services, _ = ingest_env
    chase, first, second = chase_twice(services, fixtures)
    gone, kept = (first, second) if removed == "first" else (second, first)
    services.ingest.delete(gone)
    assert stored_rows(services, chase.id) == 9
    assert services.statements.get(kept).status == "imported"
    assert len(services.statements.transactions(kept)) == 9
    services.ingest.delete(kept)
    assert stored_rows(services, chase.id) == 0


def test_try_again_on_a_statement_whose_rows_are_covered_elsewhere(ingest_env, fixtures):
    services, _ = ingest_env
    chase, first, second = chase_twice(services, fixtures)
    for statement_id in (second, first):  # the one that found them, then the one that stored them
        record = services.statements.get(statement_id)
        reopened = services.ingest.retry(statement_id, expected_version=record.version)
        assert reopened.status == "received" and reopened.run == record.run + 1
        drain(services)
        again = services.statements.get(statement_id)
        assert again.status == "imported" and again.stats["persist"]["inserted"] == 0
        assert len(services.statements.transactions(statement_id)) == 9
        assert stored_rows(services, chase.id) == 9
    services.ingest.delete(first)
    assert len(services.statements.transactions(second)) == 9


def test_after_the_first_is_removed_the_second_can_be_read_again(ingest_env, fixtures):
    services, _ = ingest_env
    chase, first, second = chase_twice(services, fixtures)
    services.ingest.delete(first)
    record = services.statements.get(second)
    services.ingest.retry(second, expected_version=record.version)
    drain(services)
    again = services.statements.get(second)
    assert again.status == "imported" and stored_rows(services, chase.id) == 9
    assert len(services.statements.transactions(second)) == 9


# --- I2: the account answer is kept on the statement -------------------------------------------


def asked_qif(services, fixtures):
    add_account(services, "monzo", "current", "Monzo")
    savings = add_account(services, "nationwide", "savings", "Rainy day")
    outcome = upload(services, fixtures, "qif/bank.qif")
    drain(services)
    asked = services.statements.get(outcome.record.id)
    assert asked.status == "needs_account"
    return savings, asked


def test_an_answer_survives_a_restart_before_its_job_runs(ingest_env, fixtures, tmp_path):
    services, _ = ingest_env
    savings, asked = asked_qif(services, fixtures)
    answered = services.ingest.answer_account(
        asked.id, account_id=savings.id, expected_version=asked.version
    )
    assert answered.account_answer_id == savings.id
    assert answered.account_answer_version == answered.version
    later = restart(tmp_path)  # the app stopped before the worker took the job
    try:
        later.queue.recover_running()
        later.ingest.resume_unfinished()
        queued = [j.payload for j in later.queue.list(status="queued") if j.kind == "ingest"]
        assert queued == [{"statement_id": asked.id, "resume": {"account_id": savings.id}}]
        drain(later)
        done = later.statements.get(asked.id)
        assert done.status == "imported" and done.account_id == savings.id
    finally:
        later.checkpointer.conn.close()


def test_an_answer_survives_its_job_dying(ingest_env, fixtures, tmp_path):
    services, _ = ingest_env
    savings, asked = asked_qif(services, fixtures)
    services.ingest.answer_account(asked.id, account_id=savings.id, expected_version=asked.version)
    services.queue.claim(kinds=frozenset({"ingest"}))  # taken, then the process died
    later = restart(tmp_path)
    try:
        later.queue.recover_running()  # max_attempts=1: that job is failed, not retried
        later.ingest.resume_unfinished()
        drain(later)
        assert later.statements.get(asked.id).account_id == savings.id
    finally:
        later.checkpointer.conn.close()


def test_a_job_without_the_answer_still_uses_the_one_on_the_statement(ingest_env, fixtures):
    services, _ = ingest_env
    savings, asked = asked_qif(services, fixtures)
    services.ingest.answer_account(asked.id, account_id=savings.id, expected_version=asked.version)
    # another enqueue for the same statement replaces the queued payload
    services.queue.enqueue("ingest", scope_key=asked.id, payload={"statement_id": asked.id})
    drain(services)
    done = services.statements.get(asked.id)
    assert done.status == "imported" and done.account_id == savings.id


def test_an_answer_for_a_closed_account_is_forgotten_when_the_question_comes_back(
    ingest_env, fixtures
):
    services, _ = ingest_env
    savings, asked = asked_qif(services, fixtures)
    services.ingest.answer_account(asked.id, account_id=savings.id, expected_version=asked.version)
    drain(services)
    imported = services.statements.get(asked.id)
    assert imported.status == "imported"
    services.accounts.close(savings.id, expected_version=services.accounts.get(savings.id).version)
    services.ingest.retry(imported.id, expected_version=imported.version)
    drain(services)
    again = services.statements.get(asked.id)
    assert again.status == "needs_account" and again.account_answer_id is None
    # a stray job for it asks again rather than failing on the closed account
    services.queue.enqueue("ingest", scope_key=again.id, payload={"statement_id": again.id})
    drain(services)
    assert services.statements.get(asked.id).status == "needs_account"


# --- I3: no statement text left on disk ----------------------------------------------------------

SECRETS = (
    b"Exampletown",
    b"Example Road",
    b"12345678",
    b"07-12-34",
    b"Greenbasket",
    b"Pat Example",
)


def leftovers(services, *names):
    found = {}
    for name in names:
        path = services.paths.root / name
        data = path.read_bytes() if path.exists() else b""
        found[name] = [s.decode() for s in SECRETS if s in data]
    return found


def test_no_statement_text_is_left_on_disk(ingest_env, fixtures):
    services, _ = ingest_env
    use_local_model(services)
    add_account(services, "nationwide", "current", "Nationwide", last4="5678")
    outcome = upload(services, fixtures, "pdf/current-two-page.pdf")
    drain(services)
    record = services.statements.get(outcome.record.id)
    assert record.status == "imported", (record.error, record.check_errors)
    checkpoints = ("checkpoints.db", "checkpoints.db-wal")
    assert leftovers(services, *checkpoints) == {name: [] for name in checkpoints}
    services.ingest.delete(record.id)
    assert leftovers(services, *checkpoints) == {name: [] for name in checkpoints}
    main = ("tuppence.db", "tuppence.db-wal")
    assert leftovers(services, *main) == {name: [] for name in main}


def test_a_statement_waiting_for_its_check_leaves_no_run_text_behind(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "starling", "current", "Starling")
    text = (fixtures / "csv" / "starling.csv").read_text().replace("-48.20,909.62", "-48.02,909.62")
    outcome = services.ingest.upload("starling-typo.csv", text.encode())
    drain(services)
    assert services.statements.get(outcome.record.id).status == "needs_review"
    data = b"".join(
        (services.paths.root / n).read_bytes()
        for n in ("checkpoints.db", "checkpoints.db-wal")
        if (services.paths.root / n).exists()
    )
    assert b"Home Cover" not in data and b"Greenbasket" not in data


def test_deleted_rows_are_overwritten(ingest_env):
    services, _ = ingest_env
    with services.db.connection() as conn:
        assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
    assert services.checkpointer.conn.execute("PRAGMA secure_delete").fetchone()[0] == 1


# --- I4: account types --------------------------------------------------------------------------


def debit_card_statement():
    return private_two_page_pdf(card_line="Visa debit card ending 4242")


def test_a_debit_card_on_a_current_account_statement_is_asked_about(ingest_env):
    services, _ = ingest_env
    use_local_model(services)
    current = add_account(services, "nationwide", "current", "FlexAccount", last4="5678")
    card = add_account(services, "nationwide", "credit_card", "Nationwide card")
    outcome = services.ingest.upload("flex.pdf", debit_card_statement())
    drain(services)
    asked = services.statements.get(outcome.record.id)
    assert asked.status == "needs_account" and asked.question is not None
    assert set(asked.question["candidates"]) == {current.id, card.id}
    assert asked.question["best_guess"] == current.id
    assert asked.question["prefill"]["kind"] == "current"


def test_wrong_account_moves_a_statement(ingest_env):
    services, _ = ingest_env
    use_local_model(services)
    current = add_account(services, "nationwide", "current", "FlexAccount", last4="5678")
    card = add_account(services, "nationwide", "credit_card", "Nationwide card")
    outcome = services.ingest.upload("flex.pdf", debit_card_statement())
    drain(services)
    asked = services.statements.get(outcome.record.id)
    services.ingest.answer_account(asked.id, account_id=card.id, expected_version=asked.version)
    drain(services)
    wrong = services.statements.get(asked.id)
    assert wrong.account_id == card.id and wrong.status in ("needs_review", "imported")
    with pytest.raises(VersionConflict):
        services.ingest.change_account(
            wrong.id, account_id=current.id, expected_version=wrong.version - 1
        )
    with pytest.raises(InputError):
        services.ingest.change_account(
            wrong.id, account_id="a_nope", expected_version=wrong.version
        )
    moved = services.ingest.change_account(
        wrong.id, account_id=current.id, expected_version=wrong.version
    )
    assert moved.status == "received" and moved.run == wrong.run + 1
    assert moved.account_answer_id == current.id
    drain(services)
    done = services.statements.get(wrong.id)
    assert done.status == "imported" and done.account_id == current.id, done.check_errors
    assert done.balance_verified and stored_rows(services, card.id) == 0
    assert stored_rows(services, current.id) == 9
    assert services.statements.balance_history(card.id) == []


def test_wrong_account_moves_an_imported_statement_and_its_rows(ingest_env, fixtures):
    services, _ = ingest_env
    joint = add_account(services, "chase", "current", "Joint")
    mine = add_account(services, "chase", "current", "Mine")
    outcome = upload(services, fixtures, "csv/chase.csv")
    drain(services)
    asked = services.statements.get(outcome.record.id)
    services.ingest.answer_account(asked.id, account_id=joint.id, expected_version=asked.version)
    drain(services)
    imported = services.statements.get(asked.id)
    assert imported.status == "imported" and stored_rows(services, joint.id) == 9
    services.ingest.change_account(
        imported.id, account_id=mine.id, expected_version=imported.version
    )
    drain(services)
    done = services.statements.get(imported.id)
    assert done.status == "imported" and done.account_id == mine.id
    assert (stored_rows(services, joint.id), stored_rows(services, mine.id)) == (0, 9)
    waiting = upload(services, fixtures, "csv/monzo.csv").record  # queued, not read yet
    with pytest.raises(InputError, match="still being read"):
        services.ingest.change_account(waiting.id, account_id=joint.id, expected_version=1)


# --- M1: the start-up sweep ---------------------------------------------------------------------


class Crash(BaseException):
    """Stands in for the process dying."""


def test_start_up_queues_a_lost_hand_off_and_drops_orphan_checkpoints(
    ingest_env, fixtures, monkeypatch, tmp_path
):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")

    def dying(*args, **kwargs):
        raise Crash()  # the process died after the import committed

    monkeypatch.setattr(pipeline, "hand_off", dying)
    outcome = upload(services, fixtures, "csv/monzo.csv")
    with pytest.raises(Crash):
        services.ingest.handle_job(services.queue.claim(kinds=frozenset({"ingest"})))
    monkeypatch.undo()
    record = services.statements.get(outcome.record.id)
    assert record.status == "imported" and record.analysis_state == "pending"
    assert IngestService.thread_id(record) in threads(services)
    later = restart(tmp_path)
    try:
        later.start()  # resume_unfinished, then the sweep, then the worker
        try:
            assert later.ingest.sweep() == {"analysis_queued": 0, "checkpoints_dropped": 0}
        finally:
            later.stop()
        analysis = [j for j in later.queue.list() if j.kind == ANALYSIS_JOB]
        assert [j.payload for j in analysis] == [{"statement_ids": [record.id]}]
        assert IngestService.thread_id(record) not in threads_of(later)
    finally:
        later.checkpointer.conn.close()


def threads_of(services):
    import sqlite3

    with sqlite3.connect(services.paths.checkpoints_db) as conn:
        return {r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")}


# --- concurrency and dedupe ---------------------------------------------------------------------


def test_two_overlapping_uploads_at_once_are_stored_once(ingest_env, fixtures):
    import time

    services, _ = ingest_env
    chase = add_account(services, "chase", "current", "Chase")
    reworded = (
        (fixtures / "csv" / "chase.csv")
        .read_text()
        .replace("Greenbasket Stores", "GREENBASKET STORES 0873 LONDON")
    )
    upload(services, fixtures, "csv/chase.csv")
    services.ingest.upload("b.csv", reworded.encode())
    services.start()  # two worker threads take one each
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if all(s.status == "imported" for s in services.statements.list()):
                break
            time.sleep(0.1)
    finally:
        services.stop()
    assert [s.status for s in services.statements.list()] == ["imported", "imported"]
    assert stored_rows(services, chase.id) == 9


@pytest.mark.parametrize(
    "order",
    [
        ("csv/nationwide.csv", "pdf/current-text.pdf"),
        ("pdf/current-text.pdf", "csv/nationwide.csv"),
    ],
)
def test_a_csv_and_a_pdf_of_the_same_month_are_stored_once(ingest_env, fixtures, order):
    services, _ = ingest_env
    use_local_model(services)
    account = add_account(services, "nationwide", "current", "Flex", last4="5678")
    for relative in order:
        outcome = upload(services, fixtures, relative)
        drain(services)
        record = services.statements.get(outcome.record.id)
        assert record.status == "imported", (record.error, record.check_errors)
    assert stored_rows(services, account.id) == 9


def test_identical_rows_on_one_day_are_both_kept(ingest_env):
    services, _ = ingest_env
    monzo = add_account(services, "monzo", "current", "Monzo")
    head = (
        b"Transaction ID,Date,Time,Type,Name,Emoji,Category,Amount,Currency,Local amount,"
        b"Local currency,Notes and #tags,Address,Receipt,Description,Category split,"
        b"Money Out,Money In\n"
    )
    row = (
        b"tx_%d,05/10/2026,09:00:00,Card payment,Little Cafe,,Eating out,-3.40,GBP,-3.40,GBP,,,,"
        b"LITTLE CAFE,,-3.40,\n"
    )
    outcome = services.ingest.upload("monzo.csv", head + row % 1 + row % 2)
    drain(services)
    record = services.statements.get(outcome.record.id)
    assert record.status == "imported" and record.stats["persist"]["inserted"] == 2
    assert stored_rows(services, monzo.id) == 2


# --- M2/M3: the fix-up screen ------------------------------------------------------------------


def starling_review(services, fixtures):
    add_account(services, "starling", "current", "Starling")
    text = (fixtures / "csv" / "starling.csv").read_text().replace("-48.20,909.62", "-48.02,909.62")
    outcome = services.ingest.upload("starling-typo.csv", text.encode())
    drain(services)
    review = services.statements.get(outcome.record.id)
    rows = [
        RowEdit(
            ref=r["ref"],
            date=r["date"],
            amount_pence=r["amount_pence"],
            description=r["raw_description"],
        )
        for r in review.draft["parsed"]["rows"]
    ]
    return review, rows


def test_a_line_can_be_a_row_or_skipped_once_only(ingest_env, fixtures):
    services, _ = ingest_env
    review, rows = starling_review(services, fixtures)
    save = services.ingest.save_draft
    with pytest.raises(InputError, match="more than once"):
        save(review.id, rows=[*rows, rows[0]], skipped=[], expected_version=review.version)
    with pytest.raises(InputError, match="skipped too"):
        skip = SkippedLine(ref=rows[0].ref, reason="Not mine")
        save(review.id, rows=rows, skipped=[skip], expected_version=review.version)
    with pytest.raises(InputError, match="more than once"):
        skip = SkippedLine(ref=rows[0].ref, reason="Not mine")
        save(review.id, rows=rows[1:], skipped=[skip, skip], expected_version=review.version)
    assert services.statements.get(review.id).version == review.version  # nothing saved


def test_impossible_amounts_are_refused_plainly(ingest_env, fixtures):
    services, _ = ingest_env
    review, rows = starling_review(services, fixtures)
    with pytest.raises(ValidationError):  # the route's 422
        RowEdit(ref="L3", date=dt.date(2026, 10, 3), amount_pence=10**20, description="x")
    huge = RowEdit.model_construct(
        ref=rows[1].ref, date=rows[1].date, amount_pence=MAX_PENCE + 1, description="x"
    )
    with pytest.raises(InputError, match="too large"):
        services.ingest.save_draft(
            review.id, rows=[rows[0], huge, *rows[2:]], skipped=[], expected_version=review.version
        )
    zero = rows[1].model_copy(update={"amount_pence": 0})
    with pytest.raises(InputError, match="can't be zero"):
        services.ingest.save_draft(
            review.id, rows=[rows[0], zero, *rows[2:]], skipped=[], expected_version=review.version
        )


def held_back_draft(services):
    """A statement waiting for its check with one line held back from the AI (it may show
    account details, or be a row above a page's first anchor)."""
    account = add_account(services, "monzo", "current", "Monzo")
    record = services.statements.create(
        sha256="d" * 64, ext="pdf", filename="october.pdf", kind="pdf"
    )
    doc = Document(
        kind="pdf",
        sha256="d" * 64,
        lines=[
            Line(ref="P1L1", text="Date Description Paid out Paid in Balance"),
            Line(ref="P1L2", text="02 Oct 2026 Greenbasket Stores 42.18 957.82"),
            Line(ref="P2L1", text="Little Cafe 3.40 954.42"),
        ],
        data_refs=["P1L1", "P1L2"],
        preamble_refs=["P2L1"],
        held_amount_refs=["P2L1"],
        pages=2,
    )
    parsed = ParsedStatement(
        importer="ai-read",
        period_start=dt.date(2026, 10, 1),
        period_end=dt.date(2026, 10, 31),
        opening_balance_pence=100000,
        closing_balance_pence=95442,
        rows=[
            ParsedRow(
                ref="P1L2",
                date=dt.date(2026, 10, 2),
                amount_pence=-4218,
                amount_text="42.18",
                sign_from="Paid out",
                raw_description="Greenbasket Stores",
                balance_after_pence=95782,
            )
        ],
        skipped=[SkippedLine(ref="P1L1", reason="column headings")],
    )
    services.statements.update(
        record.id,
        status="needs_review",
        account_id=account.id,
        check_errors=["A line of this statement with an amount on it was held back"],
        draft={
            "document": doc.model_dump(mode="json"),
            "parsed": parsed.model_dump(mode="json"),
            "level": "full",
            "pending_layout": None,
            "proposed_signs": {},
        },
    )
    return services.statements.get(record.id), account


def kept_rows(review):
    return [
        RowEdit(
            ref=r["ref"],
            date=r["date"],
            amount_pence=r["amount_pence"],
            description=r["raw_description"],
        )
        for r in review.draft["parsed"]["rows"]
    ]


def test_a_held_back_line_the_person_confirms_becomes_a_row(ingest_env):
    services, _ = ingest_env
    review, account = held_back_draft(services)
    skip_headings = [SkippedLine(ref="P1L1", reason="column headings")]
    undecided = services.ingest.save_draft(
        review.id, rows=kept_rows(review), skipped=skip_headings, expected_version=review.version
    )
    assert any("held back" in e for e in undecided.check_errors)  # still to be decided
    typed = RowEdit(
        ref="P2L1", date=dt.date(2026, 10, 3), amount_pence=-340, description="Little Cafe"
    )
    with pytest.raises(InputError, match="isn't a transaction"):  # only held-back lines
        services.ingest.save_draft(
            review.id,
            rows=[*kept_rows(review), typed.model_copy(update={"ref": "P9L9"})],
            skipped=skip_headings,
            expected_version=undecided.version,
        )
    added = services.ingest.save_draft(
        review.id,
        rows=[*kept_rows(review), typed],
        skipped=skip_headings,
        expected_version=undecided.version,
    )
    assert added.check_errors == []
    done = services.ingest.accept(review.id, expected_version=added.version)
    assert done.status == "imported" and stored_rows(services, account.id) == 2
    assert [t.source_ref for t in services.statements.transactions(done.id)] == ["P1L2", "P2L1"]


def test_a_held_back_line_can_be_skipped(ingest_env):
    services, _ = ingest_env
    review, _ = held_back_draft(services)
    skipped = [
        SkippedLine(ref="P1L1", reason="column headings"),
        SkippedLine(ref="P2L1", reason="Not a transaction"),
    ]
    saved = services.ingest.save_draft(
        review.id, rows=kept_rows(review), skipped=skipped, expected_version=review.version
    )
    assert not any("held back" in e for e in saved.check_errors)


# --- M5/M6 ---------------------------------------------------------------------------------------


def test_an_unreadable_api_key_gives_a_plain_message(ingest_env, fixtures, monkeypatch):
    services, scripted = ingest_env
    add_account(services, "nationwide", "current", "Nationwide", last4="5678")
    cloud_model(services, acknowledge=True)

    def unreadable(connection_id):
        raise SecretUnreadable("The saved API key can't be read. Enter it again in Settings › AI.")

    monkeypatch.setattr(services.connections, "api_key", unreadable)
    outcome = upload(services, fixtures, "pdf/current-text.pdf")
    drain(services)
    failed = services.statements.get(outcome.record.id)
    assert failed.status == "failed" and failed.error is not None
    assert failed.error.startswith("The AI model couldn't finish reading this file.")
    assert "Enter it again in Settings" in failed.error and scripted.requests == []
    job = next(j for j in services.queue.list() if j.kind == "ingest")
    assert job.status == "done"  # the job itself didn't crash


INVERTED_MAPPING = {
    "date_column": "Date",
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
CARD_EXPORT = (
    "Date,Narrative,Amount,Balance\n"
    "01/10/2026,GREENBASKET STORES,42.18,142.18\n"
    "02/10/2026,LITTLE CAFE,3.40,145.58\n"
)


def test_a_confirmed_layout_is_saved_only_with_the_import(ingest_env, monkeypatch):
    services, scripted = ingest_env
    use_local_model(services)
    card = add_account(services, "other", "credit_card", "Store card")
    scripted.replies = [{"content": json.dumps(INVERTED_MAPPING)}]
    outcome = services.ingest.upload("card.csv", CARD_EXPORT.encode())
    drain(services)
    asked = services.statements.get(outcome.record.id)
    services.ingest.answer_account(asked.id, account_id=card.id, expected_version=asked.version)
    drain(services)
    review = services.statements.get(asked.id)
    assert review.status == "needs_review" and review.draft["pending_layout"] is not None
    real_write = services.statements._write

    def failing_write(conn, statement_id, fields, expected_version):
        if fields.get("status") == "imported":
            raise RuntimeError("disk full")
        return real_write(conn, statement_id, fields, expected_version)

    monkeypatch.setattr(services.statements, "_write", failing_write)
    with pytest.raises(RuntimeError):
        services.ingest.accept(review.id, expected_version=review.version)
    assert services.layouts.learned.all() == []  # rolled back with the import
    assert services.statements.get(review.id).status == "needs_review"
    monkeypatch.undo()
    services.ingest.accept(review.id, expected_version=review.version)
    assert len(services.layouts.learned.all()) == 1


# --- Task 9 N1: a re-read keeps the rows until the new read is imported ----------------------


def _ids(services, statement_id):
    return sorted(t.id for t in services.statements.transactions(statement_id))


def test_try_again_keeps_the_rows_until_the_new_read_is_imported(ingest_env, fixtures):
    services, _ = ingest_env
    monzo = add_account(services, "monzo", "current", "Monzo")
    outcome = upload(services, fixtures, "csv/monzo.csv")
    drain(services)
    record = services.statements.get(outcome.record.id)
    before = _ids(services, record.id)
    assert record.status == "imported" and len(before) == 9
    reopened = services.ingest.retry(record.id, expected_version=record.version)
    assert reopened.status == "received"
    assert _ids(services, record.id) == before and stored_rows(services, monzo.id) == 9
    drain(services)
    again = services.statements.get(record.id)
    assert again.status == "imported"
    assert _ids(services, record.id) == before  # the same rows came back: their ids are kept
    assert again.stats["persist"]["inserted"] == 0
    assert services.statements.balance_history(monzo.id) == []  # monzo prints no balance


def test_a_re_read_that_fails_leaves_the_rows_in_place(ingest_env):
    services, _ = ingest_env
    use_local_model(services)
    account = add_account(services, "other", "current", "Probe")
    text = (
        "Example Bank plc\nDate Description Amount\n02/10/2026 Shop -5.00\n03/10/2026 Cafe -3.40\n"
    )
    outcome = services.ingest.upload("probe.txt", text.encode())
    drain(services)
    record = services.statements.get(outcome.record.id)
    if record.status == "needs_account":
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(record.id)
    before = _ids(services, record.id)
    assert record.status == "imported" and len(before) == 2
    with services.db.transaction() as conn:  # the model goes away
        conn.execute("DELETE FROM app_settings WHERE key = 'llm.simple_model'")
    services.ingest.retry(record.id, expected_version=record.version)
    drain(services)
    failed = services.statements.get(record.id)
    assert failed.status == "failed"
    assert _ids(services, record.id) == before and stored_rows(services, account.id) == 2


def test_wrong_account_keeps_the_rows_on_the_old_account_until_the_move_is_imported(
    ingest_env, fixtures
):
    services, _ = ingest_env
    joint = add_account(services, "chase", "current", "Joint")
    mine = add_account(services, "chase", "current", "Mine")
    outcome = upload(services, fixtures, "csv/chase.csv")
    drain(services)
    asked = services.statements.get(outcome.record.id)
    services.ingest.answer_account(asked.id, account_id=joint.id, expected_version=asked.version)
    drain(services)
    imported = services.statements.get(asked.id)
    services.ingest.change_account(
        imported.id, account_id=mine.id, expected_version=imported.version
    )
    assert (stored_rows(services, joint.id), stored_rows(services, mine.id)) == (9, 0)
    drain(services)
    assert (stored_rows(services, joint.id), stored_rows(services, mine.id)) == (0, 9)
    assert services.statements.balance_history(joint.id) == []


def test_a_changed_read_replaces_only_what_changed(ingest_env, fixtures):
    """Accepting a re-read with one row corrected: that row is replaced, the rest keep their
    ids, and rows another statement shares stay."""
    services, _ = ingest_env
    chase, first, second = chase_twice(services, fixtures)
    shared = set(_ids(services, first))
    record = services.statements.get(second)
    services.ingest.retry(second, expected_version=record.version)
    drain(services)
    assert set(_ids(services, second)) == shared
    services.ingest.delete(second)
    assert set(_ids(services, first)) == shared and stored_rows(services, chase.id) == 9


def test_links_while_re_reading_point_only_at_their_own_account(ingest_env, fixtures):
    services, _ = ingest_env
    chase, first, _ = chase_twice(services, fixtures)
    record = services.statements.get(first)
    services.ingest.retry(first, expected_version=record.version)
    with services.db.connection() as conn:
        orphans = conn.execute(
            'SELECT count(*) FROM "transaction" t WHERE NOT EXISTS'
            " (SELECT 1 FROM statement_transaction l WHERE l.transaction_id = t.id)"
        ).fetchone()[0]
    assert orphans == 0


def _invariants(services, label):
    """Every row is covered by a statement and owned by one that covers it; an imported
    statement links exactly the rows it persisted, all on its own account; a balance belongs
    to a statement that exists."""
    with services.db.connection() as c:
        orphan = c.execute(
            'SELECT count(*) FROM "transaction" t WHERE NOT EXISTS'
            " (SELECT 1 FROM statement_transaction l WHERE l.transaction_id = t.id)"
        ).fetchone()[0]
        wrong_owner = c.execute(
            'SELECT count(*) FROM "transaction" t WHERE NOT EXISTS'
            " (SELECT 1 FROM statement_transaction l WHERE l.transaction_id = t.id"
            " AND l.statement_id = t.statement_id)"
        ).fetchone()[0]
        cross = c.execute(
            "SELECT count(*) FROM statement_transaction l"
            ' JOIN "transaction" t ON t.id = l.transaction_id'
            " JOIN statement s ON s.id = l.statement_id"
            " WHERE s.status = 'imported' AND s.account_id != t.account_id"
        ).fetchone()[0]
        lost_balance = c.execute(
            "SELECT count(*) FROM account_balance b WHERE b.statement_id IS NOT NULL"
            " AND NOT EXISTS (SELECT 1 FROM statement s WHERE s.id = b.statement_id)"
        ).fetchone()[0]
        imported = c.execute(
            "SELECT s.stats, (SELECT count(*) FROM statement_transaction l"
            " WHERE l.statement_id = s.id) FROM statement s WHERE s.status = 'imported'"
        ).fetchall()
    mismatch = [n for stats, n in imported if json.loads(stats)["persist"]["rows"] != n]
    problems = {
        "orphan": orphan,
        "wrong_owner": wrong_owner,
        "cross": cross,
        "lost_balance": lost_balance,
        "mismatch": mismatch,
    }
    assert not any(problems.values()), (label, problems)


@pytest.mark.parametrize("seed", range(8))
def test_random_re_reads_moves_and_removals_keep_every_row_accounted_for(
    ingest_env, fixtures, seed
):
    import random

    services, _ = ingest_env
    rnd = random.Random(seed)
    use_local_model(services)
    flex = add_account(services, "nationwide", "current", "Flex", last4="5678")
    other = add_account(services, "nationwide", "savings", "Other")
    csv = (fixtures / "csv" / "nationwide.csv").read_bytes()
    pdf = (fixtures / "pdf" / "current-text.pdf").read_bytes()
    half = b"\n".join(csv.split(b"\n")[:10]) + b"\n"
    ids = {}
    for name, data in rnd.sample([("csv", csv), ("pdf", pdf), ("half", half)], 3):
        out = services.ingest.upload(name, data)
        ids[name] = out.record.id
        drain(services)
        rec = services.statements.get(out.record.id)
        if rec.status == "needs_account":
            services.ingest.answer_account(rec.id, account_id=flex.id, expected_version=rec.version)
            drain(services)
        _invariants(services, ("import", name))
    for step in range(8):
        if not ids:
            break
        name = rnd.choice(sorted(ids))
        rec = services.statements.get(ids[name])
        op = rnd.choice(["delete", "retry", "move", "move back"])
        if op == "delete":
            services.ingest.delete(rec.id)
            del ids[name]
        elif op == "retry" and rec.status in ("imported", "needs_review", "failed"):
            services.ingest.retry(rec.id, expected_version=rec.version)
        elif op.startswith("move") and rec.status in ("imported", "needs_review", "failed"):
            target = other if op == "move" else flex
            services.ingest.change_account(rec.id, account_id=target.id,
                                           expected_version=rec.version)  # fmt: skip
        _invariants(services, (seed, step, op, "before reading"))
        drain(services)
        for statement_id in list(ids.values()):
            r = services.statements.get(statement_id)
            if r.status == "needs_account":
                services.ingest.answer_account(r.id, account_id=flex.id,
                                               expected_version=r.version)  # fmt: skip
                drain(services)
            elif r.status == "needs_review":
                services.ingest.accept(r.id, expected_version=r.version)
        _invariants(services, (seed, step, op))
