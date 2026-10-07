"""API keys: OS keychain on desktops, encrypted database rows elsewhere (spec §4.6)."""

from __future__ import annotations

import contextlib
import os
import secrets as pysecrets
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database

SERVICE = "Tuppence"


class SecretStore(Protocol):
    kind: str

    def put(self, value: str, ref: str | None = None) -> str: ...
    def get(self, ref: str) -> str | None: ...
    def delete(self, ref: str) -> None: ...


def _new_ref() -> str:
    return "sec_" + pysecrets.token_hex(8)


class KeyringStore:
    kind = "keychain"

    def __init__(self, service: str = SERVICE) -> None:
        self.service = service

    def put(self, value: str, ref: str | None = None) -> str:
        import keyring

        ref = ref or _new_ref()
        keyring.set_password(self.service, ref, value)
        return ref

    def get(self, ref: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, ref)

    def delete(self, ref: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        with contextlib.suppress(PasswordDeleteError):
            keyring.delete_password(self.service, ref)


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
        return ref

    def get(self, ref: str) -> str | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT ciphertext FROM secret WHERE ref = ?", [ref]).fetchone()
        if row is None:
            return None
        try:
            return self.fernet.decrypt(bytes(row[0])).decode()
        except InvalidToken:
            return None

    def delete(self, ref: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM secret WHERE ref = ?", [ref])


def load_or_create_key(data_dir: Path, env: Mapping[str, str] = os.environ) -> bytes:
    configured = env.get("TUPPENCE_SECRET_KEY_FILE")
    if configured:
        return Path(configured).read_bytes().strip()
    path = data_dir / "secret.key"
    if path.exists():
        return path.read_bytes().strip()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def keyring_usable() -> bool:
    try:
        import keyring
        from keyring.backends import chainer, fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return False
        if isinstance(backend, chainer.ChainerBackend) and not backend.backends:
            return False
        backend.get_password(SERVICE, "__probe__")
    except Exception:  # noqa: BLE001 - any keyring failure means "don't use it"
        return False
    return True


def choose_secret_store(mode: str, db: Database, data_dir: Path) -> SecretStore:
    if mode in ("local", "desktop") and keyring_usable():
        return KeyringStore()
    return EncryptedDbStore(db, load_or_create_key(data_dir))
