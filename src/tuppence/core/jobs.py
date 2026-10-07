"""A small SQLite-backed job queue with coalescing (spec §8.3, §10.1)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from tuppence.core.clock import from_iso, to_iso, utcnow
from tuppence.core.db import Database

log = logging.getLogger("tuppence.jobs")
Handler = Callable[["Job"], "dict[str, Any] | None"]
Merge = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    scope_key: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    run_after: datetime
    error: str | None
    result: dict[str, Any] | None


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        scope_key=row["scope_key"],
        payload=json.loads(row["payload"]),
        status=row["status"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        run_after=from_iso(row["run_after"]),
        error=row["error"],
        result=json.loads(row["result"]) if row["result"] else None,
    )


class DeferJob(Exception):
    def __init__(self, reason: str, delay_s: float) -> None:
        super().__init__(reason)
        self.reason, self.delay_s = reason, delay_s


class JobQueue:
    def __init__(
        self,
        db: Database,
        *,
        clock: Callable[[], datetime] = utcnow,
        merges: dict[str, Merge] | None = None,
    ) -> None:
        self.db = db
        self.clock = clock
        self.merges = merges or {}

    def enqueue(
        self,
        kind: str,
        *,
        scope_key: str = "",
        payload: dict[str, Any] | None = None,
        debounce_s: float = 0.0,
        max_attempts: int = 3,
        merge: Merge | None = None,
    ) -> int:
        payload = payload or {}
        merge = merge or self.merges.get(kind)
        now = self.clock()
        run_after = to_iso(now + timedelta(seconds=debounce_s))
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id, payload, run_after FROM job"
                " WHERE kind = ? AND scope_key = ? AND status = 'queued'",
                [kind, scope_key],
            ).fetchone()
            if row is not None:
                merged = merge(json.loads(row["payload"]), payload) if merge else payload
                conn.execute(
                    "UPDATE job SET payload = ?, run_after = max(run_after, ?) WHERE id = ?",
                    [json.dumps(merged), run_after, row["id"]],
                )
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO job"
                " (kind, scope_key, payload, status, max_attempts, run_after, created_at)"
                " VALUES (?, ?, ?, 'queued', ?, ?, ?)",
                [kind, scope_key, json.dumps(payload), max_attempts, run_after, to_iso(now)],
            )
            return int(cur.lastrowid or 0)

    def claim(
        self, *, exclusive_kinds: frozenset[str] = frozenset(), kinds: frozenset[str] | None = None
    ) -> Job | None:
        """Claim the next ready job; with `kinds`, only jobs of those kinds are considered."""
        if kinds is not None and not kinds:
            return None
        now = to_iso(self.clock())
        clauses = ["j.status = 'queued'", "j.run_after <= ?"]
        params: list[Any] = [now]
        if kinds is not None:
            marks = ",".join("?" for _ in kinds)
            clauses.append(f"j.kind IN ({marks})")  # noqa: S608 - only '?' placeholders
            params.extend(sorted(kinds))
        if exclusive_kinds:
            marks = ",".join("?" for _ in exclusive_kinds)
            clauses.append(
                f"NOT (j.kind IN ({marks}) AND EXISTS"  # noqa: S608 - only '?' placeholders
                " (SELECT 1 FROM job r WHERE r.status = 'running' AND r.kind = j.kind))"
            )
            params.extend(sorted(exclusive_kinds))
        sql = (
            "UPDATE job SET status = 'running', started_at = ?, attempts = attempts + 1"  # noqa: S608
            f" WHERE id = (SELECT j.id FROM job j WHERE {' AND '.join(clauses)}"
            " ORDER BY j.run_after, j.id LIMIT 1) RETURNING *"
        )
        with self.db.transaction() as conn:
            row = conn.execute(sql, [now, *params]).fetchone()
        return None if row is None else _job(row)

    def complete(self, job_id: int, result: dict[str, Any] | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE job SET status = 'done', finished_at = ?, result = ?, error = NULL"
                " WHERE id = ?",
                [to_iso(self.clock()), json.dumps(result) if result is not None else None, job_id],
            )

    def fail(self, job_id: int, error: str, *, retry: bool = True) -> None:
        now = self.clock()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT attempts, max_attempts FROM job WHERE id = ?", [job_id]
            ).fetchone()
            if retry and row["attempts"] < row["max_attempts"]:
                delay = 5 * 4 ** (row["attempts"] - 1)
                self._requeue(conn, job_id, to_iso(now + timedelta(seconds=delay)), error)
            else:
                conn.execute(
                    "UPDATE job SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                    [to_iso(now), error[:2000], job_id],
                )

    def defer(self, job_id: int, reason: str, *, delay_s: float) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE job SET attempts = max(attempts - 1, 0) WHERE id = ?", [job_id])
            self._requeue(conn, job_id, to_iso(self.clock() + timedelta(seconds=delay_s)), reason)

    def _requeue(self, conn: sqlite3.Connection, job_id: int, run_after: str, note: str) -> None:
        try:
            conn.execute(
                "UPDATE job SET status = 'queued', run_after = ?, error = ? WHERE id = ?",
                [run_after, note[:2000], job_id],
            )
        except sqlite3.IntegrityError:
            self._supersede(conn, job_id, run_after)

    def _supersede(self, conn: sqlite3.Connection, job_id: int, run_after: str) -> None:
        """A newer queued job exists for this scope: merge into it (if a merge is set), then cancel."""
        old = conn.execute(
            "SELECT kind, scope_key, payload FROM job WHERE id = ?", [job_id]
        ).fetchone()
        merge = self.merges.get(old["kind"])
        if merge is not None:
            queued = conn.execute(
                "SELECT id, payload, run_after FROM job"
                " WHERE kind = ? AND scope_key = ? AND status = 'queued'",
                [old["kind"], old["scope_key"]],
            ).fetchone()
            if queued is not None:
                merged = merge(json.loads(old["payload"]), json.loads(queued["payload"]))
                conn.execute(
                    "UPDATE job SET payload = ?, run_after = ? WHERE id = ?",
                    [json.dumps(merged), min(queued["run_after"], run_after), queued["id"]],
                )
        conn.execute(
            "UPDATE job SET status = 'cancelled', finished_at = ?, error = ? WHERE id = ?",
            [to_iso(self.clock()), "Superseded by a newer queued job", job_id],
        )

    def recover_running(self) -> int:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id, attempts, max_attempts FROM job WHERE status = 'running'"
            )
            rows = rows.fetchall()
            for row in rows:
                if row["attempts"] >= row["max_attempts"]:
                    conn.execute(
                        "UPDATE job SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
                        [to_iso(self.clock()), "Stopped after repeated interruptions", row["id"]],
                    )
                else:
                    self._requeue(
                        conn, row["id"], to_iso(self.clock()), "Restarted after an interruption"
                    )
        return len(rows)

    def prune_finished(self, *, days: int = 30) -> int:
        """Delete done, failed and cancelled jobs finished more than `days` ago."""
        cutoff = to_iso(self.clock() - timedelta(days=days))
        with self.db.transaction() as conn:
            return conn.execute(
                "DELETE FROM job WHERE status IN ('done', 'failed', 'cancelled')"
                " AND finished_at IS NOT NULL AND finished_at <= ?",
                [cutoff],
            ).rowcount

    def get(self, job_id: int) -> Job:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM job WHERE id = ?", [job_id]).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job(row)

    def list(self, *, status: str | None = None, limit: int = 50) -> list[Job]:
        sql, params = "SELECT * FROM job", []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        with self.db.connection() as conn:
            rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", [*params, limit]).fetchall()
        return [_job(r) for r in rows]


class Worker:
    # Waits between retries of the post-handler bookkeeping write (about 8.7 s in all).
    retry_delays: tuple[float, ...] = (0.2, 0.5, 1.0, 2.0, 5.0)

    def __init__(
        self,
        queue: JobQueue,
        handlers: dict[str, Handler],
        *,
        exclusive_kinds: frozenset[str] = frozenset(),
        threads: int = 2,
        poll_interval: float = 0.5,
    ) -> None:
        self.queue, self.handlers = queue, handlers
        self.exclusive_kinds, self.threads, self.poll_interval = (
            exclusive_kinds,
            threads,
            poll_interval,
        )
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def run_once(self) -> bool:
        job = self.queue.claim(exclusive_kinds=self.exclusive_kinds, kinds=frozenset(self.handlers))
        if job is None:
            return False
        handler = self.handlers.get(job.kind)
        if handler is None:
            self.queue.fail(job.id, f"No handler for job kind '{job.kind}'", retry=False)
            return True
        try:
            result = handler(job)
        except DeferJob as exc:
            self._book(job, self.queue.defer, job.id, exc.reason, delay_s=exc.delay_s)
        except Exception as exc:  # noqa: BLE001 - a job failure must never kill the worker
            log.exception("job %s (%s) failed", job.id, job.kind)
            self._book(job, self.queue.fail, job.id, f"{type(exc).__name__}: {exc}")
        else:
            self._book(job, self.queue.complete, job.id, result)
        return True

    def _book(self, job: Job, write: Callable[..., None], *args: Any, **kwargs: Any) -> None:
        """Record a job's outcome, retrying briefly if the database is busy."""
        for delay in (*self.retry_delays, None):
            try:
                write(*args, **kwargs)
                return
            except sqlite3.OperationalError:
                if delay is None:
                    log.exception(
                        "could not record the outcome of job %s; it will be recovered on restart",
                        job.id,
                    )
                    return
                log.warning("database busy recording job %s; retrying", job.id)
                self._stop.wait(delay)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                worked = False
            if not worked:
                self._stop.wait(self.poll_interval)

    def start(self) -> None:
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._loop, name=f"tuppence-worker-{i}", daemon=True)
            for i in range(self.threads)
        ]
        for t in self._threads:
            t.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)


class Periodic:
    def __init__(self, queue: JobQueue, kind: str, *, scope_key: str, interval_s: float) -> None:
        self.queue, self.kind, self.scope_key, self.interval_s = queue, kind, scope_key, interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"tuppence-periodic-{kind}", daemon=True
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.queue.enqueue(self.kind, scope_key=self.scope_key)
            except Exception:  # noqa: BLE001 - retry on the next tick
                log.exception("could not enqueue periodic job %s", self.kind)
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2.0)
