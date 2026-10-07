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
    merge = lambda old, new: {"merchants": sorted(set(old["merchants"]) | set(new["merchants"]))}  # noqa: E731
    jid = q.enqueue("rereview", payload={"merchants": ["a"]}, merge=merge)
    q.enqueue("rereview", payload={"merchants": ["b"]}, merge=merge)
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
    assert q.get(unknown).status == "failed" and "No handler" in q.get(unknown).error


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
