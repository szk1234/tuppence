"""API keys: OS keychain on desktops, encrypted database rows elsewhere (spec §4.6)."""

from __future__ import annotations

import contextlib
import logging
import os
import secrets as pysecrets
import sqlite3
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database

SERVICE = "Tuppence"
META_KEY = "store"
log = logging.getLogger("tuppence")


class SecretError(Exception):
    """Base class for secret-storage problems that should be shown to the user."""


class SecretStoreUnavailable(SecretError):
    pass


class SecretKeyMissing(SecretError):
    pass


class SecretUnreadable(SecretError):
    pass


class SecretStore(Protocol):
    kind: str

    def put(self, value: str, ref: str | None = None) -> str: ...
    def get(self, ref: str) -> str | None: ...
    def delete(self, ref: str) -> None: ...


def _new_ref() -> str:
    return "sec_" + pysecrets.token_hex(8)


class KeyringStore:
    kind = "keychain"

    def __init__(self, service: str = SERVICE, db: Database | None = None) -> None:
        self.service = service
        self.db = db

    def put(self, value: str, ref: str | None = None) -> str:
        import keyring

        ref = ref or _new_ref()
        keyring.set_password(self.service, ref, value)
        if self.db is not None:
            with self.db.transaction() as conn:
                _record_kind(conn, self.kind)
        return ref

    def get(self, ref: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, ref)

    def delete(self, ref: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(self.service, ref)


class UnavailableStore:
    """Stands in when saved keys live in a keychain this session can't reach."""

    kind = "keychain"
    MESSAGE = (
        "Your saved AI keys are in this computer's keychain, which isn't available right now. "
        "Open Tuppence from your desktop session."
    )

    def put(self, value: str, ref: str | None = None) -> str:
        raise SecretStoreUnavailable(self.MESSAGE)

    def get(self, ref: str) -> str | None:
        raise SecretStoreUnavailable(self.MESSAGE)

    def delete(self, ref: str) -> None:
        raise SecretStoreUnavailable(self.MESSAGE)


class EncryptedDbStore:
    kind = "encrypted-db"

    def __init__(self, db: Database, key: bytes) -> None:
        self.db = db
        self.fernet = Fernet(key)

    def put(self, value: str, ref: str | None = None) -> str:
        ref = ref or _new_ref()
        token = self.fernet.encrypt(value.encode())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO secret (ref, ciphertext, created_at) VALUES (?, ?, ?)"
                " ON CONFLICT(ref) DO UPDATE SET ciphertext = excluded.ciphertext",
                [ref, token, to_iso(utcnow())],
            )
            _record_kind(conn, self.kind)
        return ref

    def get(self, ref: str) -> str | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT ciphertext FROM secret WHERE ref = ?", [ref]).fetchone()
        if row is None:
            return None
        try:
            return self.fernet.decrypt(bytes(row[0])).decode()
        except InvalidToken:
            raise SecretUnreadable(
                "This saved key can't be read with the current secret key. Re-enter it."
            ) from None

    def delete(self, ref: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM secret WHERE ref = ?", [ref])


def _record_kind(conn: sqlite3.Connection, kind: str) -> None:
    conn.execute("INSERT OR IGNORE INTO secret_meta (key, value) VALUES (?, ?)", [META_KEY, kind])


def _recorded_kind(db: Database) -> str | None:
    with db.connection() as conn:
        row = conn.execute("SELECT value FROM secret_meta WHERE key = ?", [META_KEY]).fetchone()
    return None if row is None else str(row[0])


def _has_secrets(db: Database) -> bool:
    with db.connection() as conn:
        return conn.execute("SELECT 1 FROM secret LIMIT 1").fetchone() is not None


def _checked(key: bytes, where: str, source: str) -> bytes:
    try:
        Fernet(key)
    except (ValueError, TypeError):
        raise SecretError(
            f"{source} {where} isn't a valid secret key (expected a 44-character Fernet key)."
        ) from None
    return key


def _read_key_file(path: Path, source: str) -> bytes:
    try:
        raw = path.read_bytes().strip()
    except FileNotFoundError:
        raise SecretError(f"{source} {path} doesn't exist.") from None
    except OSError as exc:
        raise SecretError(f"{source} {path} can't be read ({exc.strerror or exc}).") from None
    if not raw:
        raise SecretError(f"{source} {path} is empty.")
    return _checked(raw, str(path), source)


def _write_new_key(path: Path) -> bytes:
    """Create `path` atomically with mode 0600; if another process got there first, use its key."""
    key = Fernet.generate_key()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{pysecrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return _read_key_file(path, "The secret key file")
        except (OSError, NotImplementedError):  # no hard links on this filesystem
            if path.exists():
                return _read_key_file(path, "The secret key file")
            os.replace(tmp, path)
        return key
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def load_or_create_key(
    data_dir: Path, env: Mapping[str, str] = os.environ, *, has_secrets: bool = False
) -> bytes:
    configured = env.get("TUPPENCE_SECRET_KEY_FILE")
    if configured is not None:
        if not configured.strip():
            raise SecretError("TUPPENCE_SECRET_KEY_FILE is set but empty. Point it at a key file.")
        return _read_key_file(Path(configured), "TUPPENCE_SECRET_KEY_FILE points to")
    path = data_dir / "secret.key"
    if path.exists():
        raw = path.read_bytes().strip()
        if raw:
            if sys.platform != "win32" and stat.S_IMODE(path.stat().st_mode) & ~0o600:
                path.chmod(0o600)
                log.warning("secret.key was readable by other users; tightened it to 0600")
            return _checked(raw, str(path), "The secret key file")
        if has_secrets:
            raise _missing(path)
        path.unlink()  # an empty leftover from a failed write; nothing was encrypted with it
    elif has_secrets:
        raise _missing(path)
    data_dir.mkdir(parents=True, exist_ok=True)
    return _write_new_key(path)


def _missing(path: Path) -> SecretKeyMissing:
    return SecretKeyMissing(
        f"Tuppence's secret key file is missing or empty ({path}), but saved AI keys depend on "
        "it. Restore that file from a backup, or re-enter your AI keys."
    )


def _good_backend(backend: object) -> bool:
    from keyring.backends import chainer, fail, null

    if isinstance(backend, chainer.ChainerBackend):
        return any(_good_backend(b) for b in backend.backends)
    if isinstance(backend, fail.Keyring | null.Keyring):
        return False
    if type(backend).__module__.startswith("keyrings.alt"):
        return False
    try:
        return float(getattr(backend, "priority", 0)) >= 1
    except Exception:  # noqa: BLE001 - a backend that can't report a priority isn't usable
        return False


def keyring_usable() -> bool:
    """True only for a real OS keychain. Decided by type alone: never reads a secret, so there
    is no unlock prompt or hang at startup."""
    try:
        import keyring

        return _good_backend(keyring.get_keyring())
    except Exception:  # noqa: BLE001 - any keyring failure means "don't use it"
        return False


def choose_secret_store(mode: str, db: Database, data_dir: Path) -> SecretStore:
    recorded = _recorded_kind(db)
    has_rows = _has_secrets(db)
    if mode in ("local", "desktop") and recorded != "encrypted-db" and not has_rows:
        if keyring_usable():
            return KeyringStore(db=db)
        if recorded == "keychain":
            return UnavailableStore()
    return EncryptedDbStore(db, load_or_create_key(data_dir, has_secrets=has_rows))
