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
    monkeypatch.delenv("TUPPENCE_SECRET_KEY_FILE", raising=False)
    monkeypatch.setattr(sec, "keyring_usable", lambda: True)
    assert sec.choose_secret_store("desktop", db, tmp_path).kind == "keychain"
    assert sec.choose_secret_store("server", db, tmp_path).kind == "encrypted-db"
    monkeypatch.setattr(sec, "keyring_usable", lambda: False)
    assert sec.choose_secret_store("local", db, tmp_path).kind == "encrypted-db"


def _stub(priority, module=None):
    cls = type(
        "Stub",
        (KeyringBackend,),
        {
            "priority": priority,
            "get_password": lambda self, s, u: None,
            "set_password": lambda self, s, u, p: None,
            "delete_password": lambda self, s, u: None,
        },
    )
    if module:
        cls.__module__ = module
    return cls()


def test_keyring_usable_by_type(monkeypatch):
    from keyring.backends import chainer, fail, null

    def usable(backend):
        monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
        return sec.keyring_usable()

    assert usable(fail.Keyring()) is False
    assert usable(null.Keyring()) is False

    def chain(members):
        return type("Chain", (chainer.ChainerBackend,), {"backends": members})()

    assert usable(chain([])) is False
    assert usable(chain([fail.Keyring()])) is False
    assert usable(chain([_stub(5, "keyrings.alt.file")])) is False
    assert usable(chain([fail.Keyring(), _stub(5)])) is True
    assert usable(_stub(5, "keyrings.alt.file")) is False
    assert usable(_stub(5)) is True
    assert usable(_stub(0)) is False


def test_keyring_usable_never_reads_secrets(monkeypatch):
    calls = []
    backend = _stub(5)
    backend.get_password = lambda *a: calls.append(a)
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    assert sec.keyring_usable() is True
    assert calls == []


def test_store_choice_is_sticky(db, tmp_path, monkeypatch):
    monkeypatch.delenv("TUPPENCE_SECRET_KEY_FILE", raising=False)
    monkeypatch.setattr(sec, "keyring_usable", lambda: True)
    store = sec.choose_secret_store("desktop", db, tmp_path)
    monkeypatch.setattr(keyring, "set_password", lambda *a: None)
    ref = store.put("abc")
    monkeypatch.setattr(sec, "keyring_usable", lambda: False)
    unavailable = sec.choose_secret_store("desktop", db, tmp_path)
    with pytest.raises(sec.SecretStoreUnavailable, match="keychain"):
        unavailable.get(ref)
    with pytest.raises(sec.SecretStoreUnavailable):
        unavailable.put("x")
    assert not (tmp_path / "secret.key").exists()


def test_encrypted_choice_stays_when_keyring_appears(db, tmp_path, monkeypatch):
    monkeypatch.delenv("TUPPENCE_SECRET_KEY_FILE", raising=False)
    monkeypatch.setattr(sec, "keyring_usable", lambda: False)
    store = sec.choose_secret_store("desktop", db, tmp_path)
    ref = store.put("abc")
    monkeypatch.setattr(sec, "keyring_usable", lambda: True)
    again = sec.choose_secret_store("desktop", db, tmp_path)
    assert again.kind == "encrypted-db"
    assert again.get(ref) == "abc"


def test_missing_key_file_with_rows_is_refused(db, tmp_path, monkeypatch):
    monkeypatch.delenv("TUPPENCE_SECRET_KEY_FILE", raising=False)
    sec.EncryptedDbStore(db, sec.load_or_create_key(tmp_path, env={})).put("abc")
    (tmp_path / "secret.key").unlink()
    with pytest.raises(sec.SecretKeyMissing, match="secret.key"):
        sec.choose_secret_store("server", db, tmp_path)
    assert not (tmp_path / "secret.key").exists()
    (tmp_path / "secret.key").write_bytes(b"")
    with pytest.raises(sec.SecretKeyMissing):
        sec.load_or_create_key(tmp_path, env={}, has_secrets=True)


def test_empty_key_file_without_rows_is_repaired(tmp_path):
    (tmp_path / "secret.key").write_bytes(b"")
    key = sec.load_or_create_key(tmp_path, env={})
    assert key == (tmp_path / "secret.key").read_bytes().strip()
    assert key


def test_undecryptable_secret_raises(db, tmp_path):
    from cryptography.fernet import Fernet

    ref = sec.EncryptedDbStore(db, Fernet.generate_key()).put("abc")
    other = sec.EncryptedDbStore(db, Fernet.generate_key())
    with pytest.raises(sec.SecretUnreadable, match="Re-enter"):
        other.get(ref)
    assert other.get("sec_missing") is None


@pytest.mark.parametrize("content", ["missing", b"", b"not-a-key", b"   \n"])
def test_bad_env_key_file_names_path(tmp_path, content):
    path = tmp_path / "k"
    if content != "missing":
        path.write_bytes(content)
    with pytest.raises(sec.SecretError, match=str(path)):
        sec.load_or_create_key(tmp_path, env={"TUPPENCE_SECRET_KEY_FILE": str(path)})


def test_empty_env_var_is_an_error(tmp_path):
    with pytest.raises(sec.SecretError, match="TUPPENCE_SECRET_KEY_FILE"):
        sec.load_or_create_key(tmp_path, env={"TUPPENCE_SECRET_KEY_FILE": ""})


def test_malformed_existing_key_file_names_path(tmp_path):
    (tmp_path / "secret.key").write_bytes(b"garbage")
    with pytest.raises(sec.SecretError, match="secret.key"):
        sec.load_or_create_key(tmp_path, env={})


def test_failed_write_leaves_no_key_file(tmp_path, monkeypatch):
    def boom(_fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disk full"):
        sec.load_or_create_key(tmp_path, env={})
    assert list(tmp_path.iterdir()) == []


def test_creation_race_uses_the_winners_key(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    winner = Fernet.generate_key()
    real_link = os.link

    def racing_link(src, dst):
        (tmp_path / "secret.key").write_bytes(winner)
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", racing_link)
    assert sec.load_or_create_key(tmp_path, env={}) == winner
    assert [p.name for p in tmp_path.iterdir()] == ["secret.key"]


def test_no_hard_links_falls_back_to_replace(tmp_path, monkeypatch):
    def nolink(src, dst):
        raise PermissionError("no links here")

    monkeypatch.setattr(os, "link", nolink)
    key = sec.load_or_create_key(tmp_path, env={})
    assert (tmp_path / "secret.key").read_bytes() == key
    assert [p.name for p in tmp_path.iterdir()] == ["secret.key"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_loose_key_file_is_tightened(tmp_path, caplog):
    key = sec.load_or_create_key(tmp_path, env={})
    (tmp_path / "secret.key").chmod(0o644)
    with caplog.at_level("WARNING", logger="tuppence"):
        assert sec.load_or_create_key(tmp_path, env={}) == key
    assert stat.S_IMODE(os.stat(tmp_path / "secret.key").st_mode) == 0o600
    assert "0600" in caplog.text
    assert key.decode() not in caplog.text
