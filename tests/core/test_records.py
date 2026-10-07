import pytest

from tuppence.core.db import Database
from tuppence.core.records import NotFound, VersionConflict, update_versioned


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    with d.connection() as conn:
        conn.execute(
            "CREATE TABLE thing (id TEXT PRIMARY KEY, name TEXT, "
            "version INTEGER NOT NULL DEFAULT 1, updated_at TEXT)"
        )
        conn.execute("INSERT INTO thing (id, name) VALUES ('a', 'old')")
    return d


def test_update_bumps_version(db):
    with db.transaction() as conn:
        assert (
            update_versioned(
                conn, "thing", "id", "a", 1, {"name": "new"}, now="2026-10-07T00:00:00Z"
            )
            == 2
        )
    with db.connection() as conn:
        row = conn.execute("SELECT name, version, updated_at FROM thing").fetchone()
    assert tuple(row) == ("new", 2, "2026-10-07T00:00:00Z")


def test_stale_version_raises_conflict_with_current(db):
    with db.transaction() as conn:
        update_versioned(conn, "thing", "id", "a", 1, {"name": "x"}, now="t")
    with pytest.raises(VersionConflict) as exc, db.transaction() as conn:
        update_versioned(conn, "thing", "id", "a", 1, {"name": "y"}, now="t")
    assert exc.value.current == 2


def test_missing_row_raises_not_found(db):
    with pytest.raises(NotFound), db.transaction() as conn:
        update_versioned(conn, "thing", "id", "zzz", 1, {"name": "y"}, now="t")


def test_rejects_unsafe_identifiers(db):
    with pytest.raises(ValueError), db.transaction() as conn:
        update_versioned(conn, "thing; DROP TABLE thing", "id", "a", 1, {"name": "y"}, now="t")
    with pytest.raises(ValueError), db.transaction() as conn:
        update_versioned(conn, "thing", "id", "a", 1, {"name = 'x', version": "y"}, now="t")


def test_composite_key_update(tmp_path):
    d = Database(tmp_path / "c.db")
    with d.transaction() as conn:
        conn.execute(
            "CREATE TABLE m (a TEXT, b TEXT, name TEXT, version INTEGER NOT NULL DEFAULT 1, "
            "updated_at TEXT, PRIMARY KEY (a, b))"
        )
        conn.execute("INSERT INTO m (a, b, name) VALUES ('x', 'y/z', 'old'), ('x', 'w', 'other')")
    with d.transaction() as conn:
        assert (
            update_versioned(conn, "m", ("a", "b"), ("x", "y/z"), 1, {"name": "new"}, now="t") == 2
        )
    with pytest.raises(VersionConflict) as exc, d.transaction() as conn:
        update_versioned(conn, "m", ("a", "b"), ("x", "y/z"), 1, {"name": "q"}, now="t")
    assert exc.value.current == 2
    with pytest.raises(NotFound), d.transaction() as conn:
        update_versioned(conn, "m", ("a", "b"), ("x", "nope"), 1, {"name": "q"}, now="t")
    with pytest.raises(ValueError), d.transaction() as conn:
        update_versioned(conn, "m", ("a", "b"), ("x",), 1, {"name": "q"}, now="t")
    with d.connection() as conn:
        rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT b, name, version FROM m")}
    assert rows == {"y/z": ("new", 2), "w": ("other", 1)}


def test_tuple_key_columns_need_a_tuple_key(tmp_path):
    d = Database(tmp_path / "k.db")
    with d.transaction() as conn:
        conn.execute(
            "CREATE TABLE m (a TEXT, b TEXT, version INTEGER NOT NULL DEFAULT 1, updated_at TEXT)"
        )
        conn.execute("INSERT INTO m (a, b) VALUES ('x', 'y')")
    with pytest.raises(TypeError), d.transaction() as conn:
        update_versioned(conn, "m", ("a", "b"), "xy", 1, {"a": "q"}, now="t")
