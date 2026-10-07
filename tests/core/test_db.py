import sqlite3

import pytest

from tuppence.core.clock import from_iso, to_iso, utcnow
from tuppence.core.db import Database, connect, transaction


def test_connect_sets_pragmas(tmp_path):
    conn = connect(tmp_path / "t.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert isinstance(conn.execute("SELECT 1 AS x").fetchone(), sqlite3.Row)
    finally:
        conn.close()


def test_transaction_commits_and_rolls_back(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.connection() as conn:
        conn.execute("CREATE TABLE t (v INTEGER)")
    with db.transaction() as conn:
        conn.execute("INSERT INTO t VALUES (1)")
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("INSERT INTO t VALUES (2)")
        raise RuntimeError("boom")
    with db.connection() as conn:
        assert [r[0] for r in conn.execute("SELECT v FROM t")] == [1]


def test_nested_transaction_helper_on_open_connection(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("CREATE TABLE t (v INTEGER)")
    with transaction(conn):
        conn.execute("INSERT INTO t VALUES (5)")
    assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    conn.close()


def test_iso_roundtrip():
    now = utcnow()
    assert now.microsecond == 0
    s = to_iso(now)
    assert s.endswith("Z") and len(s) == 20
    assert from_iso(s) == now
