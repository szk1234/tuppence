"""Users, sessions and login throttling (spec §3.1, §14.2)."""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from pydantic import BaseModel

from tuppence.core.clock import from_iso, to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import UserFacing

_hasher = PasswordHasher()
# Each argon2 call allocates ~64 MiB; cap concurrency so a burst of logins can't exhaust memory.
_hash_slots = threading.BoundedSemaphore(2)
_slot_owner = threading.local()
MIN_PASSWORD = 10
MAX_PASSWORD = 1024
MAX_USERNAME = 64


HASH_WAIT_SECONDS = 5.0


class SetupComplete(RuntimeError):
    pass


class AuthBusy(RuntimeError):
    """Too many password checks are already running."""


@contextmanager
def hash_slot() -> Iterator[None]:
    """Hold one argon2 slot, waiting at most HASH_WAIT_SECONDS (else AuthBusy, body not run).

    Re-entrant within a thread: a caller can reserve the slot before its cheap checks, and the
    hash/verify calls inside reuse it instead of queueing for a second one. Never hold it across
    slow work, and never wait for it while holding the database write lock.
    """
    if getattr(_slot_owner, "held", False):
        yield
        return
    if not _hash_slots.acquire(timeout=HASH_WAIT_SECONDS):
        raise AuthBusy
    _slot_owner.held = True
    try:
        yield
    finally:
        _slot_owner.held = False
        _hash_slots.release()


class WeakPassword(UserFacing, ValueError):
    pass


def hash_password(password: str) -> str:
    with hash_slot():
        return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        with hash_slot():
            return _hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_new_password(username: str, password: str) -> None:
    if not 1 <= len(username.strip()) <= MAX_USERNAME:
        raise WeakPassword(f"Choose a username between 1 and {MAX_USERNAME} characters.")
    if len(password) < MIN_PASSWORD:
        raise WeakPassword(f"Use at least {MIN_PASSWORD} characters for your password.")
    if password.strip().lower() == username.strip().lower():
        raise WeakPassword("Your password can't be the same as your username.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class User(BaseModel):
    id: int
    username: str
    is_admin: bool


class Users:
    def __init__(self, db: Database) -> None:
        self.db = db

    def count(self) -> int:
        with self.db.connection() as conn:
            return int(conn.execute("SELECT count(*) FROM app_user").fetchone()[0])

    def create(self, username: str, password: str, *, is_admin: bool) -> User:
        username = username.strip()
        validate_new_password(username, password)
        password_hash = hash_password(password)  # slow: do it before taking the write lock
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO app_user (username, password_hash, is_admin, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [username, password_hash, int(is_admin), now, now],
            )
            user_id = int(cur.lastrowid or 0)
        return User(id=user_id, username=username, is_admin=is_admin)

    def create_first_admin(self, username: str, password: str) -> User:
        username = username.strip()
        validate_new_password(username, password)
        password_hash = hash_password(password)  # slow: do it before taking the write lock
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            if int(conn.execute("SELECT count(*) FROM app_user").fetchone()[0]) > 0:
                raise SetupComplete
            cur = conn.execute(
                "INSERT INTO app_user (username, password_hash, is_admin, created_at, updated_at)"
                " VALUES (?, ?, 1, ?, ?)",
                [username, password_hash, now, now],
            )
            user_id = int(cur.lastrowid or 0)
        return User(id=user_id, username=username, is_admin=True)

    def authenticate(self, username: str, password: str) -> User | None:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, is_admin FROM app_user WHERE username = ?",
                [username.strip()],
            ).fetchone()
        if row is None:
            hash_password(password)  # keep timing similar for unknown users
            return None
        if not verify_password(row["password_hash"], password):
            return None
        return User(id=row["id"], username=row["username"], is_admin=bool(row["is_admin"]))

    def get(self, user_id: int) -> User | None:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT id, username, is_admin FROM app_user WHERE id = ?", [user_id]
            ).fetchone()
        if row is None:
            return None
        return User(id=row["id"], username=row["username"], is_admin=bool(row["is_admin"]))


class Session(BaseModel):
    kind: Literal["user", "launch"]
    user_id: int | None
    csrf_token: str
    expires_at: str
    renewed: bool = False


class Sessions:
    def __init__(self, db: Database, *, ttl_days: int = 30) -> None:
        self.db = db
        self.ttl = timedelta(days=ttl_days)

    def create(
        self, kind: Literal["user", "launch"], user_id: int | None = None
    ) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = utcnow()
        expires = to_iso(now + self.ttl)
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO session"
                " (token_hash, kind, user_id, csrf_token, created_at, expires_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [_token_hash(token), kind, user_id, csrf, to_iso(now), expires, to_iso(now)],
            )
        return token, Session(kind=kind, user_id=user_id, csrf_token=csrf, expires_at=expires)

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        key = _token_hash(token)
        now = utcnow()
        with self.db.connection() as conn:  # read-only fast path: no write lock per request
            row = conn.execute("SELECT * FROM session WHERE token_hash = ?", [key]).fetchone()
        if row is None:
            return None
        expired = from_iso(row["expires_at"]) <= now
        stale = now - from_iso(row["last_seen_at"]) > timedelta(hours=1)
        expires = row["expires_at"]
        if expired:
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM session WHERE token_hash = ?", [key])
            return None
        if stale:
            expires = to_iso(now + self.ttl)
            with self.db.transaction() as conn:
                updated = conn.execute(
                    "UPDATE session SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                    [to_iso(now), expires, key],
                ).rowcount
            if updated == 0:  # logged out between our read and the renewal
                return None
        return Session(
            kind=row["kind"],
            user_id=row["user_id"],
            csrf_token=row["csrf_token"],
            expires_at=expires,
            renewed=stale,
        )

    def delete(self, token: str | None) -> None:
        if token:
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM session WHERE token_hash = ?", [_token_hash(token)])

    def purge_kind(self, kind: Literal["user", "launch"]) -> int:
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM session WHERE kind = ?", [kind]).rowcount


class LoginLimiter:
    """Login throttling per key (the route uses "ip|username").

    Keys are stored as SHA-256 hex digests: fixed-size rows whatever a client sends, and no
    usernames or addresses at rest.
    """

    def __init__(self, db: Database, *, max_failures: int = 5, lockout_seconds: int = 60) -> None:
        self.db = db
        self.max_failures = max_failures
        self.lockout = timedelta(seconds=lockout_seconds)

    @staticmethod
    def _stored(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def retry_after(self, key: str) -> int | None:
        key = self._stored(key)
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT locked_until FROM login_attempt WHERE key = ?", [key]
            ).fetchone()
        if row is None or row["locked_until"] is None:
            return None
        remaining = (from_iso(row["locked_until"]) - utcnow()).total_seconds()
        return math.ceil(remaining) if remaining > 0 else None

    def begin_attempt(self, key: str) -> int | None:
        """Charge an attempt up front, atomically. Returns seconds to wait if locked, else None."""
        key = self._stored(key)
        now = utcnow()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT failures, locked_until FROM login_attempt WHERE key = ?", [key]
            ).fetchone()
            failures = 0
            if row is not None:
                if row["locked_until"]:
                    remaining = (from_iso(row["locked_until"]) - now).total_seconds()
                    if remaining > 0:
                        return math.ceil(remaining)
                else:
                    failures = int(row["failures"])
            failures += 1
            locked = to_iso(now + self.lockout) if failures >= self.max_failures else None
            conn.execute(
                "INSERT INTO login_attempt (key, failures, locked_until, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET failures = excluded.failures,"
                " locked_until = excluded.locked_until, updated_at = excluded.updated_at",
                [key, failures, locked, to_iso(now)],
            )
        return None

    def record_failure(self, key: str) -> None:
        key = self._stored(key)
        now = utcnow()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT failures, locked_until FROM login_attempt WHERE key = ?", [key]
            ).fetchone()
            failures = 1 if row is None else int(row["failures"]) + 1
            if row is not None and row["locked_until"] and from_iso(row["locked_until"]) <= now:
                failures = 1
            locked = to_iso(now + self.lockout) if failures >= self.max_failures else None
            conn.execute(
                "INSERT INTO login_attempt (key, failures, locked_until, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET failures = excluded.failures,"
                " locked_until = excluded.locked_until, updated_at = excluded.updated_at",
                [key, failures, locked, to_iso(now)],
            )

    def reset(self, key: str) -> None:
        key = self._stored(key)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM login_attempt WHERE key = ?", [key])


def prune_auth(db: Database, *, now: datetime | None = None) -> dict[str, int]:
    """Delete expired sessions and login-throttle rows that are unlocked and over a day old."""
    now = now or utcnow()
    stamp = to_iso(now)
    with db.transaction() as conn:
        sessions = conn.execute("DELETE FROM session WHERE expires_at <= ?", [stamp]).rowcount
        attempts = conn.execute(
            "DELETE FROM login_attempt WHERE (locked_until IS NULL OR locked_until <= ?)"
            " AND updated_at <= ?",
            [stamp, to_iso(now - timedelta(days=1))],
        ).rowcount
    return {"sessions": sessions, "login_attempts": attempts}
