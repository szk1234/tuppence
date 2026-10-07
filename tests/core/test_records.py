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
