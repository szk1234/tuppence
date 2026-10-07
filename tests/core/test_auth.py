import pytest

from tuppence.core.auth import (
    LoginLimiter,
    Sessions,
    Users,
    WeakPassword,
    hash_password,
    validate_new_password,
    verify_password,
)
from tuppence.core.db import Database
from tuppence.core.migrate import migrate


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    migrate(d, tmp_path / "b")
    return d


def test_password_hash_roundtrip():
    h = hash_password("correct-horse-battery")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct-horse-battery")
    assert not verify_password(h, "wrong-password-1")


def test_password_policy():
    with pytest.raises(WeakPassword):
        validate_new_password("alex", "short")
    with pytest.raises(WeakPassword):
        validate_new_password("alexander1", "alexander1")
    validate_new_password("alex", "a-much-longer-passphrase")


def test_users_create_and_authenticate(db):
    users = Users(db)
    assert users.count() == 0
    u = users.create("Alex", "correct-horse-battery", is_admin=True)
    assert u.is_admin and users.count() == 1
    assert users.authenticate("alex", "correct-horse-battery").id == u.id  # case-insensitive
    assert users.authenticate("alex", "nope-nope-nope") is None
    assert users.authenticate("nobody", "correct-horse-battery") is None


def test_sessions_create_get_delete(db):
    sessions = Sessions(db)
    token, s = sessions.create("launch")
    assert len(token) >= 32 and s.kind == "launch" and len(s.csrf_token) >= 32
    assert sessions.get(token).csrf_token == s.csrf_token
    assert sessions.get("not-a-token") is None
    sessions.delete(token)
    assert sessions.get(token) is None


def test_expired_session_is_rejected(db):
    sessions = Sessions(db, ttl_days=0)
    token, _ = sessions.create("launch")
    assert sessions.get(token) is None


def test_purge_kind(db):
    sessions = Sessions(db)
    t1, _ = sessions.create("launch")
    users = Users(db)
    u = users.create("alex", "correct-horse-battery", is_admin=True)
    t2, _ = sessions.create("user", u.id)
    assert sessions.purge_kind("launch") == 1
    assert sessions.get(t1) is None and sessions.get(t2) is not None


def test_login_limiter_locks_after_five_failures(db):
    limiter = LoginLimiter(db, max_failures=5, lockout_seconds=60)
    key = "127.0.0.1|alex"
    for _ in range(5):
        assert limiter.retry_after(key) is None
        limiter.record_failure(key)
    wait = limiter.retry_after(key)
    assert wait is not None and 0 < wait <= 60
    limiter.reset(key)
    assert limiter.retry_after(key) is None
