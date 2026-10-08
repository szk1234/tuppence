"""An analysis run's life when things go wrong (fix round 1, R-M4-6): stopped by a restart
while another run waits, failing for good, a budget that can't be built, a statement removed
while the AI is answering, and each specialist's own time limit."""

from datetime import date

import pytest

from agents.helpers import file_as
from agents.test_analysis import Env
from tuppence.ingest.handoff import ANALYSIS_JOB, enqueue_analysis, request_analysis
from tuppence.ingest.store import StatementStore
from tuppence.llm.budget import RunBudget


class PowerCut(BaseException):
    """Stands in for the process dying (nothing in Tuppence catches it)."""


@pytest.fixture
def env(aenv, tmp_path):
    e = Env(aenv, tmp_path)
    yield e
    e.conn.close()


def one_off_rows(k, account_id="a_current"):
    """A statement of three one-off payments: once filed, nothing makes them stale."""
    statement = k.add_statement(account_id, date(2026, 10, 1), date(2026, 10, 31))
    for day, pence, text in ((3, -4218, "GREENBASKET STORES"), (5, -340, "LITTLE CAFE"),
                             (20, -2890, "NORTHLINE RAIL")):  # fmt: skip
        k.add_txn(date(2026, 10, day), pence, text, statement_id=statement)
    return statement


def die_in_transfers(env, monkeypatch, error=PowerCut):
    real = env.transfers.run
    state = {"die": True}

    def die(*args, **kwargs):
        if state.pop("die", False):
            raise error()
        return real(*args, **kwargs)

    monkeypatch.setattr(env.transfers, "run", die)


def start(env):
    env.queue.expedite(ANALYSIS_JOB, scope_key="household")
    job = env.queue.claim()
    assert job is not None
    return job


def prompts_since(env, n):
    return "\n".join(m for call in env.k.llm.calls[n:] for m in call["messages"])


def run_row(env, run_id):
    return next(r for r in env.service.runs(limit=100) if r.id == run_id)


# --- I1: a run a restart cut short while another run was waiting ---------------------------


def test_a_run_stopped_by_a_restart_while_another_waits_is_closed_and_its_work_done(
    env, monkeypatch
):
    """The restart can't put the interrupted job back (another analysis job is queued), so
    it folds it into that one: its run is closed, and the queued job analyses its statement
    too, without asking the AI again about rows it had already filed."""
    first = one_off_rows(env.k)
    second = env.k.add_statement("a_current", date(2026, 11, 1), date(2026, 11, 30))
    env.k.add_txn(date(2026, 11, 2), -1250, "HARBOUR PHARMACY", statement_id=second)
    enqueue_analysis(env.queue, first)
    j1 = start(env)
    die_in_transfers(env, monkeypatch)
    with pytest.raises(PowerCut):
        env.service.handle_job(j1)
    enqueue_analysis(env.queue, second)  # imported while the run was going
    asked = len(env.k.llm.calls)
    assert asked >= 1
    # --- the restart ---
    env.queue.recover_running()
    assert env.queue.get(j1.id).status == "cancelled"  # folded into the queued job
    env.service.sweep()
    stopped = run_row(env, f"ar_{j1.id}")
    assert (stopped.status, stopped.stopped_reason) == ("failed", "interrupted")
    assert stopped.summary == "Tuppence stopped during this run." and stopped.finished_at
    env.drain()
    survivor = env.service.runs()[0]
    assert survivor.status == "done" and survivor.statement_ids == sorted([first, second])
    sent = prompts_since(env, asked)
    assert "HARBOUR PHARMACY" in sent
    for text in ("GREENBASKET STORES", "LITTLE CAFE", "NORTHLINE RAIL"):
        assert text not in sent  # filed before the restart: not asked again
    with env.k.db.connection() as conn:
        assert {r[0] for r in conn.execute("SELECT analysis_state FROM statement")} == {"done"}
        assert (
            conn.execute("SELECT COUNT(*) FROM analysis_run WHERE status = 'running'").fetchone()[0]
            == 0
        )


def test_a_run_stopped_by_restarts_once_too_often_is_closed(env, monkeypatch):
    """After repeated interruptions the restart fails the job: its run doesn't stay
    `running` for ever."""
    enqueue_analysis(env.queue, one_off_rows(env.k))
    job = start(env)
    die_in_transfers(env, monkeypatch)
    with pytest.raises(PowerCut):
        env.service.handle_job(job)
    with env.k.db.transaction() as conn:  # its last attempt
        conn.execute("UPDATE job SET attempts = max_attempts WHERE id = ?", [job.id])
    env.queue.recover_running()
    assert env.queue.get(job.id).status == "failed"
    assert env.service.sweep() == 1  # its checkpoints go too
    row = run_row(env, f"ar_{job.id}")
    assert (row.status, row.stopped_reason, row.summary) == (
        "failed",
        "interrupted",
        "Tuppence stopped during this run.",
    )
    assert env.threads() == set()


def test_a_run_that_fails_on_its_last_try_is_closed(env, monkeypatch):
    enqueue_analysis(env.queue, one_off_rows(env.k))

    def broken(*args, **kwargs):
        raise RuntimeError("a bug")

    monkeypatch.setattr(env.transfers, "run", broken)
    for attempt in (1, 2, 3):
        env.drain()  # the worker records each failure; the job is retried after a pause
        row = env.service.runs()[0]
        assert row.status == "failed" and row.finished_at
        if attempt < 3:
            assert row.summary == "Something went wrong during this run. Tuppence will try again."
    [job] = [j for j in env.queue.list() if j.kind == ANALYSIS_JOB]
    assert job.status == "failed" and job.attempts == 3
    assert row.summary == (
        "Something went wrong during this run, so it stopped after 3 tries. The next run picks"
        " up what's left."
    )
    assert env.threads() == set()  # nothing will carry on from its checkpoints


# --- M3: a budget that can't be built --------------------------------------------------------


def test_a_problem_building_the_budget_marks_the_run_failed(env, monkeypatch):
    def unreadable():
        raise RuntimeError("settings unreadable")

    request_analysis(env.queue, "test")
    job = start(env)
    monkeypatch.setattr(env.service, "run_cap_gbp", unreadable)
    with pytest.raises(RuntimeError):
        env.service.handle_job(job)
    row = run_row(env, f"ar_{job.id}")  # recorded, though the run never began
    assert row.status == "failed" and row.triggers == ["test"] and row.finished_at


def test_a_resumed_run_whose_budget_cant_be_built_is_not_left_running(env, monkeypatch):
    enqueue_analysis(env.queue, one_off_rows(env.k))
    job = start(env)
    die_in_transfers(env, monkeypatch)
    with pytest.raises(PowerCut):
        env.service.handle_job(job)
    assert run_row(env, f"ar_{job.id}").status == "running"  # the process died

    def unreadable():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(env.service, "run_cap_gbp", unreadable)
    with pytest.raises(RuntimeError):
        env.service.handle_job(job)
    assert run_row(env, f"ar_{job.id}").status == "failed"


# --- M1: a statement removed while the AI is answering about its rows ------------------------


def test_rows_removed_while_the_ai_answers_are_skipped(env, monkeypatch):
    removed = one_off_rows(env.k)
    kept = env.k.add_statement("a_current", date(2026, 11, 1), date(2026, 11, 30))
    pharmacy = env.k.add_txn(date(2026, 11, 2), -1250, "HARBOUR PHARMACY", statement_id=kept)
    enqueue_analysis(env.queue, removed)
    enqueue_analysis(env.queue, kept)
    real = env.k.llm.structured
    state = {"armed": True}

    def structured(task, messages, schema, **kw):
        out = real(task, messages, schema, **kw)
        if state.pop("armed", False):
            StatementStore(env.k.db).delete(removed)  # the person removed it meanwhile
        return out

    monkeypatch.setattr(env.k.llm, "structured", structured)
    env.drain()
    [job] = [j for j in env.queue.list() if j.kind == ANALYSIS_JOB]
    assert job.status == "done" and job.attempts == 1  # not an error: the run carried on
    run = env.service.runs()[0]
    assert run.status == "done"
    assert env.k.understanding.get(pharmacy).status != "unknown"


def test_payments_removed_while_the_ai_labels_them_are_skipped(env, monkeypatch):
    statement = env.k.add_statement("a_current", date(2026, 1, 1), date(2026, 4, 30))
    gone = env.k.add_statement("a_current", date(2026, 1, 1), date(2026, 4, 30))
    for month in (1, 2, 3, 4):
        t = env.k.add_txn(date(2026, month, 5), -1500, "MYSTERY CLUB", statement_id=statement)
        file_as(env.k, t, "other", env.k.merchants)
        u = env.k.add_txn(date(2026, month, 9), -2500, "PUZZLE BOX", statement_id=gone)
        file_as(env.k, u, "other", env.k.merchants)
    real = env.k.llm.structured

    def structured(task, messages, schema, **kw):
        if "PAYMENTS:" in messages[-1].content:  # the label call
            StatementStore(env.k.db).delete(gone)
            return schema.model_validate(
                {
                    "payments": [
                        {"ref": "P1", "kind": "subscription"},
                        {"ref": "P2", "kind": "subscription"},
                    ]
                }
            )
        return real(task, messages, schema, **kw)

    monkeypatch.setattr(env.k.llm, "structured", structured)
    request_analysis(env.queue, "test")
    env.drain()
    [job] = [j for j in env.queue.list() if j.kind == ANALYSIS_JOB]
    assert job.status == "done" and job.attempts == 1
    assert [c.name for c in env.store.list()] == ["Mystery Club"]


# --- I2: each specialist's own time limit starts with its step ---------------------------------


def test_commitments_has_its_own_time_after_a_slow_categoriser(env, monkeypatch):
    """The Categoriser takes 310 s of a slow local model; Commitments' own 300 s start when
    its step does, so it still labels (the run's own limit is the sum of theirs)."""
    now = [1000.0]
    env.service.monotonic = lambda: now[0]
    statement = env.k.add_statement("a_current", date(2026, 1, 1), date(2026, 4, 30))
    for month in (1, 2, 3, 4):
        t = env.k.add_txn(date(2026, month, 5), -1500, "MYSTERY CLUB", statement_id=statement)
        file_as(env.k, t, "other", env.k.merchants)
    env.k.add_txn(date(2026, 4, 9), -340, "LITTLE CAFE", statement_id=statement)
    real = env.k.llm.structured
    seen = {}

    def structured(task, messages, schema, *, run=None, **kw):
        if "PAYMENTS:" in messages[-1].content:
            seen["label"] = run
            return real(task, messages, schema, run=run, **kw)
        out = real(task, messages, schema, run=run, **kw)
        now[0] += 310  # a slow model
        return out

    monkeypatch.setattr(env.k.llm, "structured", structured)
    enqueue_analysis(env.queue, statement)
    env.drain()
    run = env.service.runs()[0]
    assert run.status == "done", run.summary
    assert run.counts["commitments"]["labelled"] == 1 and seen["label"].calls == 1
    own, whole = seen["label"].parts
    assert own.remaining_seconds() == 300 and whole.remaining_seconds() < 1200 - 300


def test_each_specialist_budget_starts_when_it_is_first_handed_out(env):
    now = [0.0]
    env.service.monotonic = lambda: now[0]
    context = env.service.context("ar_1")
    now[0] = 500.0
    commitments = context.budget("commitments")
    own, whole = commitments.parts
    assert own.remaining_seconds() == 300 and whole.remaining_seconds() == 1200 - 500
    assert context.budget("commitments") is commitments  # one budget for the whole step
    assert isinstance(own, RunBudget)
