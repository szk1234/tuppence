import contextlib
import socket
import sqlite3
from datetime import date

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.helpers import file_as, manifest
from tuppence.agents import analysis as analysis_module
from tuppence.agents.analysis import (
    CARRY_DAYS,
    AnalysisDeps,
    AnalysisGraph,
    AnalysisService,
    summarise,
)
from tuppence.agents.commitments import Commitments, CommitmentsDeps
from tuppence.agents.transfers import TransferMatcher
from tuppence.core.jobs import JobQueue, Worker
from tuppence.ingest.handoff import (
    ANALYSIS_JOB,
    enqueue_analysis,
    merge_analysis_payload,
    merge_statement_ids,
    request_analysis,
)
from tuppence.knowledge.commitments import CommitmentStore
from tuppence.llm.types import AllModelsFailed, BudgetExceeded, NoModelConfigured


class Env:
    def __init__(self, aenv, tmp_path, *, run_cap=1.0):
        self.k = aenv
        self.queue = JobQueue(aenv.db, merges={ANALYSIS_JOB: merge_analysis_payload})
        self.conn = sqlite3.connect(tmp_path / "checkpoints.db", check_same_thread=False)
        self.checkpointer = SqliteSaver(self.conn)
        self.store = CommitmentStore(aenv.db)
        self.transfers = TransferMatcher(
            aenv.db, aenv.understanding, aenv.versions, lambda: manifest("transfer_matcher")
        )
        self.commitments = Commitments(
            CommitmentsDeps(
                db=aenv.db,
                merchants=aenv.merchants,
                store=self.store,
                llm=aenv.llm,
                context_window=aenv.window_for,
                manifest=lambda: manifest("commitments"),
            )
        )
        manifests = {"categoriser": aenv.categoriser_manifest}

        def get(name):
            return manifests.get(name) or manifest(name)

        graph = AnalysisGraph(
            AnalysisDeps(
                db=aenv.db,
                versions=aenv.versions,
                categoriser=aenv.categoriser(),
                transfers=self.transfers,
                commitments=self.commitments,
                manifest=get,
            )
        )
        self.service = AnalysisService(
            db=aenv.db,
            graph=graph,
            checkpointer=self.checkpointer,
            queue=self.queue,
            manifest=get,
            run_cap_gbp=lambda: run_cap,
        )
        self.worker = Worker(
            self.queue,
            {ANALYSIS_JOB: self.service.handle_job},
            exclusive_kinds=frozenset({ANALYSIS_JOB}),
        )

    def drain(self):
        self.queue.expedite(ANALYSIS_JOB, scope_key="household")
        while self.worker.run_once():
            pass

    def threads(self):
        return {r[0] for r in self.conn.execute("SELECT DISTINCT thread_id FROM checkpoints")}


@pytest.fixture
def env(aenv, tmp_path):
    e = Env(aenv, tmp_path)
    yield e
    e.conn.close()


def three_months(k):
    k.add_account("a_savings", "savings", owners=["p_alex"], nickname="Rainy day")
    current = k.add_statement("a_current", date(2026, 8, 1), date(2026, 10, 31))
    savings = k.add_statement("a_savings", date(2026, 8, 1), date(2026, 10, 31))
    for month in (8, 9, 10):
        k.add_txn(date(2026, month, 14), -999, "PAYPAL *STREAMLY", statement_id=current)
        k.add_txn(date(2026, month, 3), -4218, "GREENBASKET STORES 0873", statement_id=current)
        k.add_txn(
            date(2026, month, 1), -14200, "NORTHFIELD COUNCIL COUNCIL TAX", statement_id=current
        )
        k.add_txn(date(2026, month, 20), -20000, "TRANSFER TO RAINY DAY", statement_id=current)
        k.add_txn(
            date(2026, month, 20),
            20000,
            "FROM CURRENT ACCOUNT",
            account_id="a_savings",
            statement_id=savings,
        )
    return [current, savings]


def test_payload_merge_keeps_ids_and_reasons():
    assert merge_statement_ids({"statement_ids": ["s_2"]}, {"statement_ids": ["s_1"]}) == {
        "statement_ids": ["s_1", "s_2"]
    }
    assert merge_analysis_payload({"statement_ids": ["s_1"]}, {"reasons": ["rule_changed"]}) == {
        "statement_ids": ["s_1"],
        "reasons": ["rule_changed"],
    }


def test_a_new_statement_is_understood_end_to_end(env):
    for statement_id in three_months(env.k):
        enqueue_analysis(env.queue, statement_id)
    assert len(env.queue.list(status="queued")) == 1  # one pending job per household
    env.drain()
    [run] = env.service.runs()
    assert run.status == "done" and run.statement_ids and run.triggers == ["statement_imported"]
    assert run.counts["categoriser"]["rule"] == 3 and run.counts["transfers"]["pairs"] == 3
    assert "Matched 3 transfers" in run.summary and "Found 2 new bills" in run.summary
    assert {c.name for c in env.store.list()} == {"Streamly", "Northfield Council Council Tax"}
    with env.k.db.connection() as conn:
        assert {r[0] for r in conn.execute("SELECT analysis_state FROM statement")} == {"done"}
    calls = len(env.k.llm.calls)
    env.service.request("daily", now=True)
    env.drain()
    assert len(env.k.llm.calls) == calls  # nothing new: no AI calls the second time
    assert env.threads() == set()  # each finished run's checkpoints are deleted


def test_a_crash_carries_on_from_the_checkpoint(env, monkeypatch):
    ids = three_months(env.k)
    enqueue_analysis(env.queue, ids[0])
    real = env.transfers.run
    state = {"crash": True}

    def crash_once(*args, **kwargs):
        if state.pop("crash", False):
            raise RuntimeError("power cut")
        return real(*args, **kwargs)

    monkeypatch.setattr(env.transfers, "run", crash_once)
    env.queue.expedite(ANALYSIS_JOB, scope_key="household")
    job = env.queue.claim()
    with pytest.raises(RuntimeError):
        env.service.handle_job(job)
    assert env.service.runs()[0].status == "failed"
    calls = len(env.k.llm.calls)
    out = env.service.handle_job(job)
    assert out["run_id"] == f"ar_{job.id}" and len(env.k.llm.calls) == calls  # not re-asked
    assert env.service.runs()[0].status == "done"
    assert env.service.runs()[0].counts["categoriser"]["llm"] > 0  # its work before the crash


def test_work_merged_into_a_retried_job_gets_a_run_of_its_own(env, monkeypatch):
    """A failed run waits to be retried; a statement imported meanwhile (and the person's
    change) merge into its job. The retry carries on with what it began, then queues a run
    for what it didn't."""
    first, second = three_months(env.k)
    enqueue_analysis(env.queue, first)
    real = env.transfers.run
    state = {"crash": True}

    def crash_once(*args, **kwargs):
        if state.pop("crash", False):
            raise RuntimeError("power cut")
        return real(*args, **kwargs)

    monkeypatch.setattr(env.transfers, "run", crash_once)
    env.queue.expedite(ANALYSIS_JOB, scope_key="household")
    job = env.queue.claim()
    with pytest.raises(RuntimeError):
        env.service.handle_job(job)
    env.queue.fail(job.id, "power cut")  # queued again, to be retried
    enqueue_analysis(env.queue, second)
    request_analysis(env.queue, "correction")
    env.queue.expedite(ANALYSIS_JOB, scope_key="household")
    retried = env.queue.claim()
    assert retried.id == job.id
    assert retried.payload == {"statement_ids": sorted([first, second]), "reasons": ["correction"]}
    env.queue.complete(retried.id, env.service.handle_job(retried))
    [run] = env.service.runs()
    assert run.status == "done" and run.statement_ids == [first]
    [follow] = [j for j in env.queue.list(status="queued") if j.kind == ANALYSIS_JOB]
    assert follow.payload == {"statement_ids": [second], "reasons": ["correction"]}
    env.drain()
    assert env.service.runs()[0].statement_ids == [second]
    with env.k.db.connection() as conn:
        assert {r[0] for r in conn.execute("SELECT analysis_state FROM statement")} == {"done"}


def test_budget_stop_is_partial_and_the_rest_waits(env):
    ids = three_months(env.k)
    env.k.llm.script = [BudgetExceeded("This run reached its limit of 0 AI calls.")]
    enqueue_analysis(env.queue, ids[0])
    env.drain()
    [run] = env.service.runs()
    assert run.status == "partial" and run.stopped_reason == "budget"
    assert "wait for the next" in run.summary
    assert env.service.waiting().get("deferred", 0) >= 1


def test_summary_wording():
    assert summarise({}) == "Nothing new to sort."
    text = summarise(
        {"categoriser": {"rule": 2, "llm": 5, "stopped": "awaiting_ai", "awaiting_ai": 4}}
    )
    assert text.startswith("Sorted 7 transactions (2 by your rules, 5 by the AI).")
    assert "4 transactions are waiting for an AI model" in text


# --- G5: an AI problem ends the run partial, with the reason ---------------------------------


def test_the_summary_says_why_the_ai_wasnt_used_even_with_nothing_waiting():
    """A refile (or a commitment label) that hit an AI problem leaves no transaction waiting:
    the summary gives the reason without claiming "0 transactions are waiting"."""
    text = summarise(
        {
            "categoriser": {"rule": 2, "stopped": "awaiting_ai", "ai_problem": "No model here."},
            "waiting": {"deferred": 0, "awaiting_ai": 0},
        }
    )
    assert "0 transactions" not in text
    assert "The AI couldn't be used this time" in text and "No model here." in text
    commitments_only = summarise(
        {"commitments": {"stopped": "awaiting_ai", "awaiting_ai": 2, "ai_problem": "Blocked."}}
    )
    assert "Blocked." in commitments_only and "transactions are waiting" not in commitments_only
    budget = summarise({"commitments": {"stopped": "budget", "deferred": 1}, "waiting": {}})
    assert "the rest waits for the next run" in budget and "0 transactions" not in budget
    assert "1 regular payment waits for the AI" in budget


def test_the_summary_counts_each_waiting_transaction_once():
    """The specialists' counts overlap (a row sorted, then left waiting for its second look,
    counted by both passes): the run's own count of the rows left waiting wins."""
    text = summarise(
        {
            "categoriser": {
                "llm": 5,
                "deferred": 4,
                "awaiting_ai": 6,
                "stopped": "awaiting_ai",
                "ai_problem": "Local only is on.",
            },
            "waiting": {"deferred": 1, "awaiting_ai": 3},
        }
    )
    assert "4 transactions are waiting for an AI model" in text and "Local only is on." in text
    budget = summarise(
        {"categoriser": {"stopped": "budget", "deferred": 9}, "waiting": {"deferred": 2}}
    )
    assert "2 transactions wait for the next" in budget


def test_the_summary_reports_texts_hidden_because_masking_failed():
    assert summarise({"categoriser": {"scrub_failures": 0}}) == "Nothing new to sort."
    text = summarise({"categoriser": {"llm": 1, "scrub_failures": 2}})
    assert "2 texts were sent as <HIDDEN>" in text


def test_no_model_ends_the_run_partial_with_the_rows_awaiting_ai(env):
    ids = three_months(env.k)
    env.k.llm.script = [NoModelConfigured("Choose an AI model in Settings › AI.")] * 20
    enqueue_analysis(env.queue, ids[0])
    env.drain()
    [job] = [j for j in env.queue.list() if j.kind == ANALYSIS_JOB]
    assert job.status == "done"  # an AI problem never fails the job
    [run] = env.service.runs()
    assert run.status == "partial" and run.stopped_reason == "awaiting_ai"
    assert "transactions are waiting for an AI model" in run.summary
    assert "Choose an AI model in Settings › AI." in run.summary
    assert env.service.waiting().get("awaiting_ai", 0) >= 1


def test_an_ai_problem_in_commitments_alone_ends_the_run_partial(env):
    """The Categoriser needs no AI (every row already filed); the Commitments label call
    fails: the run is partial, with that reason, and nothing is said to be waiting."""
    statement = env.k.add_statement("a_current", date(2026, 1, 1), date(2026, 4, 30))
    for month in (1, 2, 3, 4):
        t = env.k.add_txn(date(2026, month, 5), -1500, "MYSTERY CLUB", statement_id=statement)
        file_as(env.k, t, "other", env.k.merchants)
    env.k.llm.script = [AllModelsFailed(["Example: timed out"])]
    enqueue_analysis(env.queue, statement)
    env.drain()
    [run] = env.service.runs()
    assert run.counts["commitments"]["awaiting_ai"] == 1
    assert run.status == "partial" and run.stopped_reason == "awaiting_ai"
    assert "No AI model could answer: Example: timed out" in run.summary
    assert "transactions are waiting" not in run.summary


def test_a_stored_commitment_and_a_dismissal_survive_a_run_without_ai(env):
    """A commitment whose kind now needs the AI's label (the person re-filed its payments
    under "other") is kept while the AI can't be asked, and "Not a commitment" sticks."""
    for statement_id in three_months(env.k):
        enqueue_analysis(env.queue, statement_id)
    env.drain()
    stored = {c.name: c for c in env.store.list()}
    assert set(stored) == {"Streamly", "Northfield Council Council Tax"}
    council = stored["Northfield Council Council Tax"]
    env.store.set_dismissed(council.id, council.version, True)
    for t in env.store.payment_ids(stored["Streamly"].id):
        row = env.k.understanding.get(t)
        env.k.understanding.set_by_person(t, expected_version=row.version, category_id="other")
    env.k.llm.script = [NoModelConfigured("Choose an AI model in Settings › AI.")] * 20
    env.service.request("correction", now=True)
    env.drain()
    run = env.service.runs()[0]
    assert run.status == "partial" and run.counts["commitments"]["awaiting_ai"] == 1
    assert [c.id for c in env.store.list()] == [stored["Streamly"].id]
    dismissed = [c for c in env.store.list(include_dismissed=True) if c.dismissed]
    assert [c.id for c in dismissed] == [council.id]


def test_the_run_passes_the_commitments_budget(env, monkeypatch):
    seen = {}
    real = env.commitments.run

    def spy(*, run_id, budget):
        seen["budget"] = budget
        return real(run_id=run_id, budget=budget)

    monkeypatch.setattr(env.commitments, "run", spy)
    request_analysis(env.queue, "test")
    env.drain()
    assert seen["budget"] is not None and seen["budget"].own.max_calls == 20  # its manifest's


# --- budgets ---------------------------------------------------------------------------------


def test_each_specialist_runs_inside_the_whole_runs_budget(aenv, tmp_path):
    e = Env(aenv, tmp_path, run_cap=0.2)
    try:
        context = e.service.context("ar_1")
        categoriser, commitments = context.budget("categoriser"), context.budget("commitments")
        own, run = categoriser.parts
        assert (own.max_calls, own.max_gbp) == (60, 0.2)  # its manifest, money capped per run
        assert (commitments.parts[0].max_calls, commitments.parts[0].max_gbp) == (20, 0.15)
        assert commitments.parts[1] is run  # one run budget shared by both
        assert (run.max_calls, run.max_tokens, run.max_gbp) == (80, 500_000, 0.2)
    finally:
        e.conn.close()


# --- scope: the statements' rows (M3's link table) and the backlog ------------------------------


def test_a_statements_rows_come_from_the_link_table(env):
    """A row first imported from another statement and found again by this one ("exact") is
    in this statement's scope (G8)."""
    first = env.k.add_statement("a_current", date(2026, 10, 1), date(2026, 10, 31))
    second = env.k.add_statement("a_current", date(2026, 10, 1), date(2026, 10, 31))
    t = env.k.add_txn(date(2026, 10, 3), -4218, "GREENBASKET STORES", statement_id=first)
    with env.k.db.transaction() as conn:
        conn.execute(
            "INSERT INTO statement_transaction (statement_id, transaction_id, source_ref, match)"
            " VALUES (?, ?, 'L9', 'exact')",
            [second, t],
        )
    enqueue_analysis(env.queue, second)
    env.drain()
    [run] = env.service.runs()
    assert run.counts["categoriser"]["scope"] == 1 and run.statement_ids == [second]
    assert env.k.understanding.get(t).status != "unknown"


def test_queued_rows_join_the_next_run(env):
    t = env.k.add_txn(date(2026, 10, 3), -4218, "GREENBASKET STORES")
    with env.k.db.transaction() as conn:
        env.k.understanding.set_waiting(conn, [t], "awaiting_ai")
    env.service.request("model_added", now=True)
    env.drain()
    [run] = env.service.runs()
    assert run.triggers == ["model_added"] and run.counts["categoriser"]["scope"] == 1
    assert env.k.understanding.get(t).waiting is None


# --- R-M4-2: corrections kept over a re-read are kept for 90 days -------------------------------


def test_old_carried_corrections_are_dropped(env):
    with env.k.db.transaction() as conn:
        for fp, age in (("fp_old", CARRY_DAYS + 1), ("fp_recent", CARRY_DAYS - 1)):
            conn.execute(
                "INSERT INTO understanding_carry (account_id, fingerprint, category_id, who,"
                " is_transfer, ignored, evidence, carried_at) VALUES ('a_current', ?, 'other',"
                " NULL, 0, 0, '{}', strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?))",
                [fp, f"-{age} days"],
            )
    t = env.k.add_txn(date(2026, 10, 3), -4218, "GREENBASKET STORES")
    env.k.understanding.set_by_person(t, expected_version=1, category_id="food.groceries")
    with env.k.db.transaction() as conn:  # carried by the trigger just now
        conn.execute('DELETE FROM "transaction" WHERE id = ?', [t])
    request_analysis(env.queue, "test")
    env.drain()
    with env.k.db.connection() as conn:
        left = {r[0] for r in conn.execute("SELECT fingerprint FROM understanding_carry")}
    assert "fp_old" not in left and "fp_recent" in left and len(left) == 2
    [run] = env.service.runs()
    assert run.counts["housekeeping"] == {"carries_dropped": 1}


# --- checkpoints ---------------------------------------------------------------------------------


def test_the_sweep_drops_only_finished_runs_checkpoints(env, monkeypatch):
    """At start-up: a queued or running job's thread stays (it resumes); the thread of a job
    that has gone (done, failed, cancelled, pruned) is dropped; statement threads are left to
    the ingest sweep."""
    real = env.transfers.run
    state = {"crash": True}

    def crash_once(*args, **kwargs):
        if state.pop("crash", False):
            raise RuntimeError("power cut")
        return real(*args, **kwargs)

    monkeypatch.setattr(env.transfers, "run", crash_once)
    request_analysis(env.queue, "test")
    env.queue.expedite(ANALYSIS_JOB, scope_key="household")
    job = env.queue.claim()
    with pytest.raises(RuntimeError):
        env.service.handle_job(job)
    live = f"analysis:{job.id}"
    config = {"configurable": {"thread_id": "statement:s_x:1", "checkpoint_ns": ""}}
    gone = {"configurable": {"thread_id": "analysis:999", "checkpoint_ns": ""}}
    for cfg in (config, gone):
        env.checkpointer.put(cfg, _empty_checkpoint(), {}, {})
    assert env.threads() == {live, "statement:s_x:1", "analysis:999"}
    assert env.service.sweep() == 1
    assert env.threads() == {live, "statement:s_x:1"}
    env.queue.fail(job.id, "power cut", retry=False)  # its job has gone now
    assert env.service.sweep() == 1
    assert env.threads() == {"statement:s_x:1"}


def _empty_checkpoint():
    from langgraph.checkpoint.base import empty_checkpoint

    return empty_checkpoint()


# --- G7: no tracing --------------------------------------------------------------------------


def _connects_while_analysing(env, monkeypatch, *, flush=True):
    """Run an analysis with LangSmith tracing switched on in the environment, counting every
    network connection attempted."""
    import time

    import langsmith.utils
    from langchain_core.tracers.langchain import wait_for_all_tracers

    three_months(env.k)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    langsmith.utils.get_env_var.cache_clear()
    attempts = []

    def no_connect(self, address):
        attempts.append(address)
        raise OSError("no network in this test")

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    try:
        request_analysis(env.queue, "test")
        env.drain()
        if flush:
            wait_for_all_tracers()
        else:
            deadline = time.monotonic() + 30
            while not attempts and time.monotonic() < deadline:
                time.sleep(0.05)
        assert env.service.runs()[0].status == "done"
    finally:
        monkeypatch.undo()
        langsmith.utils.get_env_var.cache_clear()
    return attempts


def test_an_analysis_run_is_never_traced(env, monkeypatch):
    assert _connects_while_analysing(env, monkeypatch) == []


def test_without_the_guard_an_analysis_run_would_reach_langsmith(env, monkeypatch):
    """The control: with the guard taken out, the same run tries to send itself to LangSmith."""
    monkeypatch.setattr(
        analysis_module, "tracing_context", lambda **kwargs: contextlib.nullcontext()
    )
    assert _connects_while_analysing(env, monkeypatch, flush=False)
