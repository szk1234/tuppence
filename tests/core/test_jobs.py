from datetime import timedelta

import pytest

from tuppence.core.clock import utcnow
from tuppence.core.db import Database
from tuppence.core.jobs import DeferJob, JobQueue, Worker
from tuppence.core.migrate import migrate


class Clock:
    def __init__(self):
        self.now = utcnow()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    clock = Clock()
    return JobQueue(db, clock=clock), clock


def test_enqueue_coalesces_same_scope(env):
    q, _ = env
    a = q.enqueue("analysis", scope_key="acct-1", payload={"n": 1})
    b = q.enqueue("analysis", scope_key="acct-1", payload={"n": 2})
    assert a == b and q.get(a).payload == {"n": 2}
    c = q.enqueue("analysis", scope_key="acct-2")
    assert c != a


def test_merge_function_combines_payloads(env):
    q, _ = env
    q.merges["rereview"] = lambda old, new: {
        "merchants": sorted(set(old["merchants"]) | set(new["merchants"]))
    }
    jid = q.enqueue("rereview", payload={"merchants": ["a"]})
    q.enqueue("rereview", payload={"merchants": ["b"]})
    assert q.get(jid).payload == {"merchants": ["a", "b"]}


def test_debounce_delays_claim(env):
    q, clock = env
    q.enqueue("analysis", debounce_s=30)
    assert q.claim() is None
    clock.advance(31)
    assert q.claim() is not None


def test_exclusive_kind_runs_one_at_a_time(env):
    q, _ = env
    q.enqueue("analysis", scope_key="a")
    q.enqueue("analysis", scope_key="b")
    q.enqueue("other")
    ex = frozenset({"analysis"})
    first = q.claim(exclusive_kinds=ex)
    second = q.claim(exclusive_kinds=ex)
    assert first.kind == "analysis" and second.kind == "other"
    assert q.claim(exclusive_kinds=ex) is None
    q.complete(first.id)
    assert q.claim(exclusive_kinds=ex).scope_key == "b"


def test_failure_retries_with_backoff_then_fails(env):
    q, clock = env
    jid = q.enqueue("x", max_attempts=2)
    q.fail(q.claim().id, "boom")
    job = q.get(jid)
    assert job.status == "queued" and job.attempts == 1 and job.error == "boom"
    clock.advance(6)
    q.fail(q.claim().id, "boom again")
    assert q.get(jid).status == "failed"


def test_running_job_requeued_after_restart(env):
    q, _ = env
    jid = q.enqueue("analysis")
    q.claim()
    assert q.get(jid).status == "running"
    assert q.recover_running() == 1
    assert q.get(jid).status == "queued"


def test_requeue_does_not_clash_with_newer_queued_job(env):
    q, _ = env
    first = q.enqueue("analysis", scope_key="s")
    q.claim()
    second = q.enqueue("analysis", scope_key="s")
    assert second != first
    q.recover_running()
    assert q.get(first).status == "cancelled" and q.get(second).status == "queued"


def test_worker_runs_handlers_and_records_results(env):
    q, clock = env
    seen = []
    ok = q.enqueue("echo", payload={"v": 1})
    bad = q.enqueue("explode", max_attempts=1)
    later = q.enqueue("later")
    unknown = q.enqueue("mystery", max_attempts=1)

    def defer(job):
        raise DeferJob("waiting for data", delay_s=60)

    w = Worker(
        q,
        {
            "echo": lambda j: seen.append(j.payload) or {"ok": True},
            "explode": lambda j: 1 / 0,
            "later": defer,
        },
    )
    while w.run_once():
        pass
    assert seen == [{"v": 1}] and q.get(ok).status == "done" and q.get(ok).result == {"ok": True}
    assert q.get(bad).status == "failed" and "division by zero" in q.get(bad).error
    assert q.get(later).status == "queued" and q.get(later).error == "waiting for data"
    assert q.get(unknown).status == "queued"  # kinds without a handler are left alone


def test_worker_thread_start_stop(env):
    import time

    q, _ = env
    done = []
    w = Worker(q, {"echo": lambda j: done.append(j.id)}, poll_interval=0.05)
    w.start()
    jid = q.enqueue("echo")
    deadline = time.monotonic() + 5
    while not done and time.monotonic() < deadline:
        time.sleep(0.05)
    w.stop()
    assert done == [jid]


def test_prune_auth_job_removes_expired_sessions_and_stale_attempts(tmp_path):
    from tuppence.core.auth import prune_auth
    from tuppence.core.clock import to_iso

    db = Database(tmp_path / "p.db")
    migrate(db, tmp_path / "b")
    now = utcnow()
    old, recent = to_iso(now - timedelta(days=2)), to_iso(now - timedelta(hours=1))
    past, future = to_iso(now - timedelta(seconds=5)), to_iso(now + timedelta(days=1))
    with db.transaction() as conn:
        for tok, exp in [("expired", past), ("live", future)]:
            conn.execute(
                "INSERT INTO session"
                " (token_hash, kind, csrf_token, created_at, expires_at, last_seen_at)"
                " VALUES (?, 'launch', 'c', ?, ?, ?)",
                [tok, old, exp, old],
            )
        for key, locked, upd in [
            ("stale", None, old),
            ("stale-expired-lock", past, old),
            ("fresh", None, recent),
            ("locked-old", to_iso(now + timedelta(seconds=30)), old),
        ]:
            conn.execute(
                "INSERT INTO login_attempt (key, failures, locked_until, updated_at)"
                " VALUES (?, 3, ?, ?)",
                [key, locked, upd],
            )
    q = JobQueue(db)
    q.enqueue("maintenance.prune_auth", scope_key="daily")
    w = Worker(q, {"maintenance.prune_auth": lambda j: prune_auth(db)})
    assert w.run_once()
    with db.connection() as conn:
        sessions = [r[0] for r in conn.execute("SELECT token_hash FROM session")]
        keys = sorted(r[0] for r in conn.execute("SELECT key FROM login_attempt"))
    assert sessions == ["live"] and keys == ["fresh", "locked-old"]


def test_limiter_stamps_updated_at(tmp_path):
    from tuppence.core.auth import LoginLimiter

    db = Database(tmp_path / "l.db")
    migrate(db, tmp_path / "b")
    LoginLimiter(db).begin_attempt("k")
    with db.connection() as conn:
        assert conn.execute("SELECT updated_at FROM login_attempt").fetchone()[0] > "2020"


MERGE = lambda old, new: {"ids": sorted(set(old["ids"]) | set(new["ids"]))}  # noqa: E731


def _merging_env(tmp_path):
    db = Database(tmp_path / "m.db")
    migrate(db, tmp_path / "b")
    clock = Clock()
    return JobQueue(db, clock=clock, merges={"analysis": MERGE}), clock


def _running_then_newer(q, first_ids, newer_ids):
    first = q.enqueue("analysis", scope_key="s", payload={"ids": first_ids})
    claimed = q.claim()
    assert claimed.id == first
    newer = q.enqueue("analysis", scope_key="s", payload={"ids": newer_ids})
    assert newer != first
    return first, newer


def test_registered_merge_applies_on_enqueue(tmp_path):
    q, _ = _merging_env(tmp_path)
    jid = q.enqueue("analysis", payload={"ids": [1]})
    q.enqueue("analysis", payload={"ids": [2]})
    assert q.get(jid).payload == {"ids": [1, 2]}


@pytest.mark.parametrize("path", ["fail", "defer", "recover"])
def test_requeue_conflict_keeps_both_payloads(tmp_path, path):
    q, _ = _merging_env(tmp_path)
    first, newer = _running_then_newer(q, [1], [2])
    if path == "fail":
        q.fail(first, "boom")
    elif path == "defer":
        q.defer(first, "wait", delay_s=60)
    else:
        q.recover_running()
    assert q.get(first).status == "cancelled"
    assert q.get(newer).status == "queued" and q.get(newer).payload == {"ids": [1, 2]}


def test_requeue_conflict_without_merge_drops_old_payload(env):
    q, _ = env
    first = q.enqueue("analysis", scope_key="s", payload={"ids": [1]})
    q.claim()
    newer = q.enqueue("analysis", scope_key="s", payload={"ids": [2]})
    q.fail(first, "boom")
    assert q.get(newer).payload == {"ids": [2]}


def test_recover_running_fails_poison_jobs(env):
    q, _ = env
    jid = q.enqueue("analysis", max_attempts=1)
    q.claim()
    assert q.recover_running() == 1
    job = q.get(jid)
    assert job.status == "failed" and job.error == "Stopped after repeated interruptions"


def test_claim_only_takes_listed_kinds(env):
    q, _ = env
    q.enqueue("mystery")
    assert q.claim(kinds=frozenset({"echo"})) is None
    assert q.claim(kinds=frozenset()) is None
    assert q.claim(kinds=frozenset({"mystery"})) is not None


def test_prune_finished_removes_old_terminal_jobs(env):
    q, clock = env
    done = q.enqueue("a")
    q.complete(q.claim().id)
    q.enqueue("b", max_attempts=1)
    q.fail(q.claim().id, "x")
    queued = q.enqueue("c")
    clock.advance(29 * 86400)
    assert q.prune_finished(days=30) == 0
    clock.advance(2 * 86400)
    q.enqueue("recent")
    assert q.prune_finished(days=30) == 2
    assert [j.kind for j in q.list()] == ["recent", "c"]
    assert done and queued


def test_periodic_survives_enqueue_errors(env):
    import sqlite3
    import time

    from tuppence.core.jobs import Periodic

    q, _ = env
    real, calls = q.enqueue, []

    def flaky(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(*a, **kw)

    q.enqueue = flaky  # type: ignore[method-assign]
    p = Periodic(q, "tick", scope_key="s", interval_s=0.05)
    p.start()
    deadline = time.monotonic() + 5
    while not q.list() and time.monotonic() < deadline:
        time.sleep(0.05)
    alive = p._thread.is_alive()
    p.stop()
    assert alive and len(calls) >= 2 and q.list()


def test_worker_retries_bookkeeping_write(env):
    import sqlite3

    q, _ = env
    jid = q.enqueue("echo")
    real, attempts = q.complete, []

    def flaky(job_id, result=None):
        attempts.append(1)
        if len(attempts) <= 2:
            raise sqlite3.OperationalError("database is locked")
        real(job_id, result)

    q.complete = flaky  # type: ignore[method-assign]
    w = Worker(q, {"echo": lambda j: {"ok": 1}})
    w.retry_delays = (0, 0, 0)
    assert w.run_once()
    assert len(attempts) == 3 and q.get(jid).status == "done"


def test_worker_gives_up_bookkeeping_after_retries(env):
    import sqlite3

    q, _ = env
    jid = q.enqueue("echo")

    def broken(job_id, result=None):
        raise sqlite3.OperationalError("database is locked")

    q.complete = broken  # type: ignore[method-assign]
    w = Worker(q, {"echo": lambda j: None})
    w.retry_delays = (0, 0)
    assert w.run_once()
    assert q.get(jid).status == "running"  # recover_running handles it on next start


@pytest.mark.parametrize("path", ["fail", "defer", "recover"])
def test_raising_merge_cancels_old_job_and_keeps_queued_payload(tmp_path, path, caplog):
    def boom(old, new):
        raise KeyError("old shape")

    q, _ = _merging_env(tmp_path)
    q.merges["analysis"] = boom
    first, newer = _running_then_newer(q, [1], [2])
    if path == "fail":
        q.fail(first, "boom")
    elif path == "defer":
        q.defer(first, "wait", delay_s=60)
    else:
        assert q.recover_running() == 1
    old = q.get(first)
    assert old.status == "cancelled" and old.error == "Superseded; its work couldn't be merged"
    assert q.get(newer).status == "queued" and q.get(newer).payload == {"ids": [2]}
    assert "[1]" not in caplog.text and "[2]" not in caplog.text


def test_idle_backoff_doubles_to_the_cap_and_resets_after_a_job(env):
    q, _ = env
    w = Worker(q, {"echo": lambda j: None}, threads=1, poll_interval=0.5, max_idle_interval=5.0)
    waits = []

    def fake_wait(seen, timeout):  # records the idle waits without sleeping
        waits.append(timeout)
        if len(waits) == 6:
            q.enqueue("echo")
        if len(waits) == 8:
            w._stop.set()
        return seen

    q.wait_for_work = fake_wait
    w._loop()
    assert waits == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0, 0.5, 1.0]


def test_enqueue_wakes_an_idle_worker_promptly_after_backoff_grew(env):
    import threading
    import time

    q, _ = env
    done = threading.Event()
    w = Worker(
        q, {"echo": lambda j: done.set()}, threads=1, poll_interval=0.25, max_idle_interval=30.0
    )
    timeouts = []
    real_wait = q.wait_for_work

    def spy(seen, timeout):
        timeouts.append(timeout)
        return real_wait(seen, timeout)

    q.wait_for_work = spy
    w.start()
    try:
        deadline = time.monotonic() + 10
        while not (timeouts and timeouts[-1] >= 2.0) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert timeouts[-1] >= 2.0  # now in a 2 s idle wait
        start = time.monotonic()
        q.enqueue("echo")
        assert done.wait(1.0)
        assert time.monotonic() - start < 1.0
    finally:
        w.stop()


def test_stop_is_prompt_during_a_long_idle_wait(env):
    import time

    q, _ = env
    w = Worker(q, {"echo": lambda j: None}, poll_interval=30.0, max_idle_interval=30.0)
    w.start()
    time.sleep(0.1)
    start = time.monotonic()
    w.stop()
    assert time.monotonic() - start < 1.0
    assert not any(t.is_alive() for t in w._threads)
