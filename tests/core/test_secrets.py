import os
import stat
import sys

import keyring
import pytest
from keyring.backend import KeyringBackend

from tuppence.core import secrets as sec
from tuppence.core.db import Database
from tuppence.core.migrate import migrate


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.data = {}

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def get_password(self, service, username):
        return self.data.get((service, username))

    def delete_password(self, service, username):
        self.data.pop((service, username), None)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    migrate(d, tmp_path / "b")
    return d


def test_encrypted_db_roundtrip_and_ciphertext(db, tmp_path):
    store = sec.EncryptedDbStore(db, sec.load_or_create_key(tmp_path, env={}))
    ref = store.put("sk-test-123")
    assert store.get(ref) == "sk-test-123"
    with db.connection() as conn:
        blob = conn.execute("SELECT ciphertext FROM secret WHERE ref = ?", [ref]).fetchone()[0]
    assert b"sk-test-123" not in blob
    store.put("sk-new", ref)
    assert store.get(ref) == "sk-new"
    store.delete(ref)
    assert store.get(ref) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_generated_key_file_is_private(tmp_path):
    sec.load_or_create_key(tmp_path, env={})
    mode = stat.S_IMODE(os.stat(tmp_path / "secret.key").st_mode)
    assert mode == 0o600
    assert (
        sec.load_or_create_key(tmp_path, env={}) == (tmp_path / "secret.key").read_bytes().strip()
    )


def test_key_file_from_env(tmp_path):
    from cryptography.fernet import Fernet

    keyfile = tmp_path / "docker-secret"
    keyfile.write_bytes(Fernet.generate_key())
    assert (
        sec.load_or_create_key(tmp_path / "data", env={"TUPPENCE_SECRET_KEY_FILE": str(keyfile)})
        == keyfile.read_bytes().strip()
    )


@pytest.fixture
def memory_keyring():
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(previous)


@pytest.mark.usefixtures("memory_keyring")
def test_keyring_store_roundtrip():
    store = sec.KeyringStore()
    ref = store.put("abc")
    assert store.get(ref) == "abc"
    store.delete(ref)
    assert store.get(ref) is None


def test_choose_store(db, tmp_path, monkeypatch):
    monkeypatch.setattr(sec, "keyring_usable", lambda: True)
    assert sec.choose_secret_store("desktop", db, tmp_path).kind == "keychain"
    assert sec.choose_secret_store("server", db, tmp_path).kind == "encrypted-db"
    monkeypatch.setattr(sec, "keyring_usable", lambda: False)
    assert sec.choose_secret_store("local", db, tmp_path).kind == "encrypted-db"
