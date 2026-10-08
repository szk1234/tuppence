"""The ingest graph end to end, through `Services`, the real job queue and checkpointer. The
oracle answers every AI call. All statements are synthetic."""

import json
import socket
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from ingest.helpers import add_account, cloud_model, drain, threads, upload, use_local_model
from tuppence.app.services import build_services
from tuppence.core.errors import InputError
from tuppence.core.household import PersonIn
from tuppence.core.records import NotFound, VersionConflict
from tuppence.ingest import pipeline
from tuppence.ingest.handoff import ANALYSIS_JOB, merge_statement_ids
from tuppence.ingest.models import SkippedLine
from tuppence.ingest.service import IngestService, RowEdit, clean_filename
from tuppence.settings import RuntimeSettings


def test_monzo_csv_is_identified_and_imported_without_ai(ingest_env, fixtures):
    services, scripted = ingest_env
    monzo = add_account(services, "monzo", "current", "Monzo")
    add_account(services, "hsbc", "current", "Bills")
    outcome = upload(services, fixtures, "csv/monzo.csv")
    assert not outcome.duplicate and outcome.record.status == "received"
    drain(services)
    record = services.statements.get(outcome.record.id)
    assert record.status == "imported" and record.account_id == monzo.id
    assert record.importer == "csv:monzo" and not record.balance_verified
    assert record.stats["persist"]["inserted"] == 9 and record.stats["llm_calls"] == 0
    assert len(services.statements.transactions(record.id)) == 9
    assert services.statements.remembered_accounts(record.layout_fingerprint) == [monzo.id]
    queued = [j for j in services.queue.list(status="queued") if j.kind == ANALYSIS_JOB]
    assert queued and queued[0].payload == {"statement_ids": [record.id]}
    assert scripted.requests == []
    assert upload(services, fixtures, "csv/monzo.csv").duplicate
    # the run's checkpoints (statement text) aren't kept once it is imported
    assert IngestService.thread_id(record) not in threads(services)


def test_card_pdf_is_read_checked_and_its_balance_recorded(ingest_env, fixtures):
    services, scripted = ingest_env
    use_local_model(services)
    card = add_account(services, "barclaycard", "credit_card", "Barclaycard", last4="4242")
    outcome = upload(services, fixtures, "pdf/card-text.pdf")
    drain(services)
    record = services.statements.get(outcome.record.id)
    assert record.status == "imported" and record.account_id == card.id, record.error
    assert record.balance_verified and record.stats["persist"]["inserted"] == 13
    assert record.stats["llm_calls"] == len(scripted.requests) > 0
    assert services.statements.balance_history(card.id)[-1][1] == -90985


@pytest.mark.parametrize(
    ("relative", "provider", "kind", "last4", "rows"),
    [
        ("ofx/current.ofx", "hsbc", "current", "2222", None),
        ("ofx/card.qfx", "amex", "credit_card", "4242", None),
        ("camt/statement.xml", "nationwide", "current", "5678", None),
        ("xlsx/statement.xlsx", "other", "current", None, 3),
    ],
)
def test_structured_files_are_imported(ingest_env, fixtures, relative, provider, kind, last4, rows):
    services, scripted = ingest_env
    account = add_account(services, provider, kind, "Main", last4=last4)
    outcome = upload(services, fixtures, relative)
    drain(services)
    record = services.statements.get(outcome.record.id)
    if record.status == "needs_account":  # nothing tied the file to the account: answer
        assert record.question["best_guess"] == account.id
        services.ingest.answer_account(
            record.id, account_id=account.id, expected_version=record.version
        )
        drain(services)
        record = services.statements.get(record.id)
    assert record.status == "imported" and record.account_id == account.id, (
        record.error,
        record.check_errors,
    )
    inserted = record.stats["persist"]["inserted"]
    assert inserted > 0 and (rows is None or inserted == rows)
    assert scripted.requests == []


def test_the_account_question_survives_a_restart_and_is_remembered(ingest_env, fixtures, tmp_path):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    savings = add_account(services, "nationwide", "savings", "Rainy day")
    outcome = upload(services, fixtures, "qif/bank.qif")
    drain(services)
    asked = services.statements.get(outcome.record.id)
    assert asked.status == "needs_account" and asked.question is not None
    assert asked.question["text"] == "Which account is this?"
    assert savings.id in asked.question["candidates"] and len(asked.question["candidates"]) == 2
    assert asked.question["best_guess"] is None  # nothing to suggest one over the other
    assert IngestService.thread_id(asked) in threads(services)  # waiting at the question
    restarted = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    try:
        restarted.ingest.answer_account(
            asked.id, account_id=savings.id, expected_version=asked.version
        )
        drain(restarted)
        done = restarted.statements.get(asked.id)
        assert done.status == "imported" and done.account_id == savings.id
        assert restarted.statements.remembered_accounts(done.layout_fingerprint) == [savings.id]
    finally:
        restarted.checkpointer.conn.close()


def test_an_answer_must_be_one_of_your_accounts(ingest_env, fixtures):
    services, _ = ingest_env
    current = add_account(services, "monzo", "current", "Monzo")
    add_account(services, "nationwide", "savings", "Rainy day")
    outcome = upload(services, fixtures, "qif/bank.qif")
    drain(services)
    asked = services.statements.get(outcome.record.id)
    with pytest.raises(InputError):
        services.ingest.answer_account(
            asked.id, account_id="a_nope", expected_version=asked.version
        )
    with pytest.raises(VersionConflict):
        services.ingest.answer_account(
            asked.id, account_id=current.id, expected_version=asked.version - 1
        )
    assert services.statements.get(asked.id).status == "needs_account"


def test_an_unanswered_question_is_asked_again_after_a_restart(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    add_account(services, "nationwide", "savings", "Rainy day")
    outcome = upload(services, fixtures, "qif/bank.qif")
    drain(services)
    asked = services.statements.get(outcome.record.id)
    services.statements.update(asked.id, status="parsing", question=None)  # killed mid-answer
    assert services.ingest.resume_unfinished() == 1
    drain(services)
    again = services.statements.get(asked.id)
    assert again.status == "needs_account" and again.question == asked.question


class Crash(BaseException):
    """Stands in for the process dying mid-run."""


def test_an_answer_given_before_the_run_had_asked_is_used(ingest_env, fixtures, monkeypatch):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    savings = add_account(services, "nationwide", "savings", "Rainy day")
    real_interrupt, crash = pipeline.interrupt, {"once": True}

    def crashing_interrupt(value):
        if crash.pop("once", False):
            raise Crash()  # the process died after the question was shown, before it was saved
        return real_interrupt(value)

    monkeypatch.setattr(pipeline, "interrupt", crashing_interrupt)
    outcome = upload(services, fixtures, "qif/bank.qif")
    with pytest.raises(Crash):
        services.ingest.handle_job(services.queue.claim())
    asked = services.statements.get(outcome.record.id)
    assert asked.status == "needs_account" and asked.question is not None
    services.ingest.answer_account(asked.id, account_id=savings.id, expected_version=asked.version)
    drain(services)
    done = services.statements.get(asked.id)
    assert done.status == "imported" and done.account_id == savings.id


def test_a_crash_mid_run_carries_on_from_the_checkpoint(ingest_env, fixtures, monkeypatch):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    extracts, real_extract, real_parse = [], pipeline.extract_document, pipeline.parse_document
    crash = {"once": True}

    def counting_extract(*args, **kwargs):
        extracts.append(1)
        return real_extract(*args, **kwargs)

    def crashing_parse(*args, **kwargs):
        if crash.pop("once", False):
            raise Crash()
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(pipeline, "extract_document", counting_extract)
    monkeypatch.setattr(pipeline, "parse_document", crashing_parse)
    outcome = upload(services, fixtures, "csv/monzo.csv")
    job = services.queue.claim()
    with pytest.raises(Crash):
        services.ingest.handle_job(job)
    assert services.statements.get(outcome.record.id).status == "parsing"
    assert services.ingest.handle_job(job)["status"] == "imported"
    assert extracts == [1]  # the restart resumed at "parse", not from the start


def test_start_up_queues_statements_left_part_way(ingest_env, fixtures, tmp_path):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    outcome = upload(services, fixtures, "csv/monzo.csv")
    services.queue.claim()  # the job was running when the app stopped
    restarted = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    try:
        restarted.queue.recover_running()
        assert restarted.ingest.resume_unfinished() == 1
        drain(restarted)
        assert restarted.statements.get(outcome.record.id).status == "imported"
    finally:
        restarted.checkpointer.conn.close()


def test_stopping_closes_the_checkpoints_once_the_worker_has_stopped(ingest_env):
    services, _ = ingest_env
    services.start()
    services.stop()
    assert not any(t.is_alive() for t in services.worker._threads)
    with pytest.raises(sqlite3.ProgrammingError):
        services.checkpointer.conn.execute("SELECT 1")


def test_a_run_cut_short_by_shutdown_carries_on_at_the_next_start(
    ingest_env, fixtures, monkeypatch, tmp_path
):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    real_parse = pipeline.parse_document

    def parse_while_shutting_down(*args, **kwargs):
        services.ingest.close()  # the app stops while this job is still running
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(pipeline, "parse_document", parse_while_shutting_down)
    outcome = upload(services, fixtures, "csv/monzo.csv")
    drain(services)
    assert services.statements.get(outcome.record.id).status == "parsing"  # not failed
    monkeypatch.setattr(pipeline, "parse_document", real_parse)
    restarted = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    try:
        restarted.queue.recover_running()
        assert restarted.ingest.resume_unfinished() == 1
        drain(restarted)
        assert restarted.statements.get(outcome.record.id).status == "imported"
    finally:
        restarted.checkpointer.conn.close()


def test_a_job_for_a_finished_statement_changes_nothing(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    outcome = upload(services, fixtures, "csv/monzo.csv")
    drain(services)
    before = services.statements.get(outcome.record.id)
    services.queue.enqueue("ingest", scope_key=before.id, payload={"statement_id": before.id})
    drain(services)
    after = services.statements.get(before.id)
    assert after.status == "imported" and after.version == before.version


def test_overlapping_statements_in_two_formats_are_not_double_counted(ingest_env, fixtures):
    services, _ = ingest_env
    chase = add_account(services, "chase", "current", "Chase")
    upload(services, fixtures, "csv/chase.csv")
    drain(services)
    reworded = (
        (fixtures / "csv" / "chase.csv")
        .read_text()
        .replace("Greenbasket Stores", "GREENBASKET STORES 0873 LONDON")
    )
    second = services.ingest.upload("chase-again.csv", reworded.encode())
    drain(services)
    record = services.statements.get(second.record.id)
    assert record.status == "imported" and record.account_id == chase.id
    persist = record.stats["persist"]
    assert (persist["inserted"], persist["duplicates_exact"], persist["duplicates_similar"]) == (
        0,
        8,
        1,
    )
    with services.db.connection() as conn:
        stored = conn.execute(
            'SELECT count(*) FROM "transaction" WHERE account_id = ?', [chase.id]
        ).fetchone()[0]
    assert stored == 9


def test_a_statement_that_does_not_add_up_is_fixed_then_imported(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "starling", "current", "Starling")
    text = (fixtures / "csv" / "starling.csv").read_text().replace("-48.20,909.62", "-48.02,909.62")
    outcome = services.ingest.upload("starling-typo.csv", text.encode())
    drain(services)
    review = services.statements.get(outcome.record.id)
    assert review.status == "needs_review" and review.draft is not None
    assert "balance mismatch" in " ".join(review.check_errors)
    assert IngestService.thread_id(review) not in threads(services)  # the draft is on the row
    rows = [
        RowEdit(
            ref=r["ref"],
            date=r["date"],
            amount_pence=-4820 if r["ref"] == "L3" else r["amount_pence"],
            description=r["raw_description"],
        )
        for r in review.draft["parsed"]["rows"]
    ]
    fixed = services.ingest.save_draft(
        review.id, rows=rows, skipped=[], expected_version=review.version
    )
    assert fixed.check_errors == []
    with pytest.raises(VersionConflict):
        services.ingest.accept(review.id, expected_version=review.version)
    done = services.ingest.accept(review.id, expected_version=fixed.version)
    assert done.status == "imported" and done.stats["accepted_with"] == []
    queued = [j for j in services.queue.list(status="queued") if j.kind == ANALYSIS_JOB]
    assert queued and done.id in queued[0].payload["statement_ids"]


def test_skipping_a_line_on_the_fix_up_screen(ingest_env, fixtures):
    services, _ = ingest_env
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
        if r["ref"] != "L3"
    ]
    edited = services.ingest.save_draft(
        review.id,
        rows=rows,
        skipped=[SkippedLine(ref="L3", reason="Not mine")],
        expected_version=review.version,
    )
    assert any("running balance mismatch" in e for e in edited.check_errors)  # the balance says no
    done = services.ingest.accept(review.id, expected_version=edited.version)
    assert done.status == "imported" and done.stats["accepted_with"] == edited.check_errors
    assert not done.balance_verified and len(services.statements.transactions(done.id)) == 8


def test_an_unknown_layout_is_learned_and_the_next_file_needs_no_ai(ingest_env, fixtures):
    services, scripted = ingest_env
    use_local_model(services)
    add_account(services, "monzo", "current", "Monzo")
    union = add_account(services, "other", "current", "Credit union")
    first = upload(services, fixtures, "csv-unknown/credit-union.csv")
    drain(services)
    asked = services.statements.get(first.record.id)
    assert asked.status == "needs_account"
    services.ingest.answer_account(asked.id, account_id=union.id, expected_version=asked.version)
    drain(services)
    done = services.statements.get(asked.id)
    assert done.status == "imported" and done.importer.startswith("csv:learned-")
    assert len(scripted.requests) == 1
    second = upload(services, fixtures, "csv-unknown/credit-union-nov.csv")
    drain(services)
    # A layout shared by two current accounts isn't account-specific, so it's still asked,
    # with the earlier answer pre-selected.
    later = services.statements.get(second.record.id)
    assert later.status == "needs_account" and later.question["best_guess"] == union.id
    services.ingest.answer_account(later.id, account_id=union.id, expected_version=later.version)
    drain(services)
    later = services.statements.get(second.record.id)
    assert later.status == "imported" and later.account_id == union.id
    assert later.importer == done.importer
    assert len(scripted.requests) == 1  # no AI call the second time


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


def card_question(services, data, name):
    outcome = services.ingest.upload(name, data)
    drain(services)
    asked = services.statements.get(outcome.record.id)
    assert asked.status == "needs_account", (asked.error, asked.check_errors)
    return asked


def test_a_layout_in_doubt_is_kept_only_once_the_person_confirms(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    card = add_account(services, "other", "credit_card", "Store card")
    scripted.replies = [{"content": json.dumps(INVERTED_MAPPING)}]
    asked = card_question(services, CARD_EXPORT.encode(), "card.csv")
    services.ingest.answer_account(asked.id, account_id=card.id, expected_version=asked.version)
    drain(services)
    review = services.statements.get(asked.id)
    assert review.status == "needs_review"
    assert any("which way round" in e for e in review.check_errors)
    assert review.draft["pending_layout"] is not None
    assert services.layouts.learned.all() == []  # not saved on its own
    done = services.ingest.accept(review.id, expected_version=review.version)
    assert done.status == "imported" and done.importer.startswith("csv:learned-")
    assert len(services.layouts.learned.all()) == 1
    nov = CARD_EXPORT.replace("/10/2026", "/11/2026")
    later = card_question(services, nov.encode(), "card-nov.csv")
    services.ingest.answer_account(later.id, account_id=card.id, expected_version=later.version)
    drain(services)
    later = services.statements.get(later.id)
    assert later.status == "imported" and later.importer == done.importer
    assert len(scripted.requests) == 1  # the layout was remembered


def test_a_layout_whose_signs_the_person_turned_round_is_not_kept(ingest_env):
    services, scripted = ingest_env
    use_local_model(services)
    card = add_account(services, "other", "credit_card", "Store card")
    scripted.replies = [{"content": json.dumps(INVERTED_MAPPING)}]
    asked = card_question(services, CARD_EXPORT.encode(), "card.csv")
    services.ingest.answer_account(asked.id, account_id=card.id, expected_version=asked.version)
    drain(services)
    review = services.statements.get(asked.id)
    flipped = [
        RowEdit(
            ref=r["ref"],
            date=r["date"],
            amount_pence=-r["amount_pence"],
            description=r["raw_description"],
        )
        for r in review.draft["parsed"]["rows"]
    ]
    edited = services.ingest.save_draft(
        review.id, rows=flipped, skipped=[], expected_version=review.version
    )
    done = services.ingest.accept(review.id, expected_version=edited.version)
    assert done.status == "imported" and services.layouts.learned.all() == []
    assert done.importer == "csv:unknown"


def test_no_ai_model_fails_clearly_and_try_again_works(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "nationwide", "current", "Nationwide", last4="5678")
    outcome = upload(services, fixtures, "pdf/current-text.pdf")
    drain(services)
    failed = services.statements.get(outcome.record.id)
    assert failed.status == "failed" and "Choose one in Settings › AI" in failed.error
    assert IngestService.thread_id(failed) not in threads(services)
    use_local_model(services)
    services.ingest.retry(failed.id, expected_version=failed.version)
    drain(services)
    done = services.statements.get(failed.id)
    assert done.status == "imported" and done.run == 2 and done.balance_verified


def test_local_only_gives_the_persons_message(ingest_env, fixtures):
    services, scripted = ingest_env
    add_account(services, "nationwide", "current", "Nationwide", last4="5678")
    cloud_model(services, acknowledge=True)
    services.settings.set("privacy.local_only", True, expected_version=0)
    outcome = upload(services, fixtures, "pdf/current-text.pdf")
    drain(services)
    failed = services.statements.get(outcome.record.id)
    assert failed.status == "failed" and "Local only" in failed.error, failed.error
    assert "Something went wrong" not in failed.error and scripted.requests == []


def test_an_unconfirmed_cloud_notice_gives_the_persons_message(ingest_env, fixtures):
    services, scripted = ingest_env
    add_account(services, "nationwide", "current", "Nationwide", last4="5678")
    cloud_model(services, acknowledge=False)
    outcome = upload(services, fixtures, "pdf/current-text.pdf")
    drain(services)
    failed = services.statements.get(outcome.record.id)
    assert failed.status == "failed" and "Confirm what" in failed.error, failed.error
    assert "Something went wrong" not in failed.error and scripted.requests == []


def test_a_damaged_file_fails_with_a_plain_message(ingest_env):
    services, _ = ingest_env
    outcome = services.ingest.upload("broken.pdf", b"%PDF-1.4\n" + b"\x00garbage" * 50)
    drain(services)
    failed = services.statements.get(outcome.record.id)
    assert failed.status == "failed" and failed.error
    assert "Traceback" not in failed.error and "Something went wrong" not in failed.error


def test_a_bug_in_a_step_fails_the_statement_with_a_plain_message(
    ingest_env, fixtures, monkeypatch
):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")

    def broken(*args, **kwargs):
        raise RuntimeError("Alex Example 12345678")  # library text must never be stored

    monkeypatch.setattr(pipeline, "parse_document", broken)
    outcome = upload(services, fixtures, "csv/monzo.csv")
    drain(services)
    failed = services.statements.get(outcome.record.id)
    assert failed.status == "failed" and failed.error is not None
    assert failed.error.startswith("Something went wrong") and "(RuntimeError)" in failed.error
    assert "12345678" not in failed.error
    job = next(j for j in services.queue.list() if j.kind == "ingest")
    assert job.status == "failed" and "12345678" not in (job.error or "")
    assert IngestService.thread_id(failed) not in threads(services)


def test_an_import_stands_if_the_analysis_hand_off_fails(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")

    def no_queue(statement_id):
        raise RuntimeError("queue unavailable")

    services.ingest.deps.on_imported = no_queue
    outcome = upload(services, fixtures, "csv/monzo.csv")
    drain(services)
    record = services.statements.get(outcome.record.id)
    assert record.status == "imported" and record.analysis_state == "pending"


def test_removing_a_statement_removes_its_rows_and_file(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    outcome = upload(services, fixtures, "csv/monzo.csv")
    drain(services)
    record = services.statements.get(outcome.record.id)
    stored = services.statement_files.path_for(record.file_sha256, record.file_ext)
    assert stored.exists()
    services.ingest.delete(record.id)
    with pytest.raises(NotFound):
        services.statements.get(record.id)
    assert services.statements.transactions(record.id) == [] and not stored.exists()


def test_removing_a_statement_waiting_for_an_answer_drops_its_run(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    add_account(services, "nationwide", "savings", "Rainy day")
    outcome = upload(services, fixtures, "qif/bank.qif")
    drain(services)
    asked = services.statements.get(outcome.record.id)
    assert IngestService.thread_id(asked) in threads(services)
    services.ingest.delete(asked.id)
    assert IngestService.thread_id(asked) not in threads(services)


def test_analysis_jobs_wait_for_the_analysis_workflow(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    add_account(services, "starling", "current", "Starling")
    first = upload(services, fixtures, "csv/monzo.csv")
    drain(services)
    second = upload(services, fixtures, "csv/starling.csv")
    drain(services)
    assert ANALYSIS_JOB not in services.worker.handlers  # M4 brings the handler
    services.queue.clock = _later  # past the 30 s debounce
    assert not services.worker.run_once()  # ready, but nobody claims it
    queued = [j for j in services.queue.list(status="queued") if j.kind == ANALYSIS_JOB]
    assert len(queued) == 1
    assert queued[0].payload == {"statement_ids": sorted([first.record.id, second.record.id])}
    assert merge_statement_ids({"statement_ids": ["s_2"]}, {"statement_ids": ["s_1"]}) == {
        "statement_ids": ["s_1", "s_2"]
    }
    for record in services.statements.list():
        assert record.analysis_state == "pending"


def _later():
    return datetime.now(UTC) + timedelta(minutes=5)


def test_household_names_reach_every_step(ingest_env, fixtures, monkeypatch):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    services.household.create_person(PersonIn(display_name="Sam Sample", role="adult"))
    seen = {}
    real_extract, real_parse = pipeline.extract_document, pipeline.parse_document

    def extract(*args, **kwargs):
        seen["extract"] = list(kwargs.get("names", ()))
        return real_extract(*args, **kwargs)

    def parse(*args, **kwargs):
        seen["parse"] = list(kwargs.get("names", ()))
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(pipeline, "extract_document", extract)
    monkeypatch.setattr(pipeline, "parse_document", parse)
    upload(services, fixtures, "csv/monzo.csv")
    drain(services)
    assert {"Alex Example", "Sam Sample"} <= set(seen["extract"])
    assert {"Alex Example", "Sam Sample"} <= set(seen["parse"])


def test_no_identity_detail_or_balance_line_reaches_the_model(ingest_env, fixtures):
    """Holder name, address, account number, sort code and card ending on page 1, all repeated
    at the top of page 2, plus balance lines: none of it is in any request."""
    services, scripted = ingest_env
    use_local_model(services)
    account = add_account(services, "nationwide", "current", "Nationwide", last4="5678")
    outcome = upload(services, fixtures, "pdf/current-two-page.pdf")
    drain(services)
    record = services.statements.get(outcome.record.id)
    assert record.status == "imported" and record.account_id == account.id, (
        record.error,
        record.check_errors,
    )
    assert record.balance_verified and record.stats["persist"]["inserted"] == 9
    sent = json.dumps(scripted.requests)
    assert scripted.requests
    for secret in (
        "Alex Example",
        "1 Example Road",
        "Exampletown",
        "EX1 2MP",
        "12345678",
        "07-12-34",
        "Card ending 4242",
        "4242",
        "Available balance",
        "Opening balance",
        "Closing balance",
    ):
        assert secret not in sent, secret


def connect_attempts_while_importing(services, fixtures, monkeypatch, *, flush=True):
    """Import a Monzo CSV with LangSmith tracing switched on in the environment, and count
    every network connection attempted (`flush`: wait for every tracer to finish; else stop
    at the first attempt)."""
    import time

    import langsmith.utils
    from langchain_core.tracers.langchain import wait_for_all_tracers

    add_account(services, "monzo", "current", "Monzo")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    # Should anything get past the blocked socket (a tracer thread outliving the test), it can
    # only reach a closed port on this machine, never the real service.
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    langsmith.utils.get_env_var.cache_clear()
    attempts = []

    def no_connect(self, address):
        attempts.append(address)
        raise OSError("no network in this test")

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    try:
        outcome = upload(services, fixtures, "csv/monzo.csv")
        drain(services)
        if flush:
            wait_for_all_tracers()
        else:
            deadline = time.monotonic() + 30
            while not attempts and time.monotonic() < deadline:
                time.sleep(0.05)
        assert services.statements.get(outcome.record.id).status == "imported"
    finally:
        monkeypatch.undo()
        langsmith.utils.get_env_var.cache_clear()
    return attempts


def test_langsmith_tracing_never_runs(ingest_env, fixtures, monkeypatch):
    services, _ = ingest_env
    assert connect_attempts_while_importing(services, fixtures, monkeypatch) == []


def test_without_the_guard_a_run_would_reach_langsmith(ingest_env, fixtures, monkeypatch):
    """The control for the test above: with the guard taken out, the same import tries to send
    its run to LangSmith, so that test can fail."""
    import contextlib

    from tuppence.ingest import service as service_module

    services, _ = ingest_env
    monkeypatch.setattr(
        service_module, "tracing_context", lambda **kwargs: contextlib.nullcontext()
    )
    assert connect_attempts_while_importing(services, fixtures, monkeypatch, flush=False)


def test_checkpoints_only_ever_hold_plain_values(ingest_env, fixtures):
    services, _ = ingest_env
    add_account(services, "monzo", "current", "Monzo")
    add_account(services, "nationwide", "savings", "Rainy day")
    outcome = upload(services, fixtures, "qif/bank.qif")
    drain(services)
    asked = services.statements.get(outcome.record.id)
    config = {"configurable": {"thread_id": IngestService.thread_id(asked)}}
    state = services.checkpointer.get_tuple(config)
    assert state is not None
    values = state.checkpoint["channel_values"]
    json.dumps({k: v for k, v in values.items() if not k.startswith("branch:")})


def test_stored_file_names_are_plain():
    assert clean_filename("C:\\Users\\alex\\Downloads\\oct.csv") == "oct.csv"
    assert clean_filename("../../etc/statement\x00\x1b.pdf") == "statement.pdf"
    assert clean_filename("   ") == "statement" and clean_filename(None) == "statement"
    assert len(clean_filename("a" * 400 + ".csv")) == 255
