import sqlite3

import pytest

from tuppence.core import migrate as mig
from tuppence.core.db import Database


def tables(db):
    with db.connection() as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_database_gets_all_migrations_and_no_backup(tmp_path):
    db = Database(tmp_path / "tuppence.db")
    applied = mig.migrate(db, tmp_path / "backups")
    assert applied[0] == "0001_core"
    expected = {"app_settings", "person", "household", "profile_entry", "schema_migrations"}
    assert expected <= tables(db)
    assert not (tmp_path / "backups").exists() or not any((tmp_path / "backups").iterdir())


def test_migrate_is_idempotent(tmp_path):
    db = Database(tmp_path / "tuppence.db")
    mig.migrate(db, tmp_path / "backups")
    assert mig.migrate(db, tmp_path / "backups") == []


def test_backup_taken_before_pending_migration_on_existing_db(tmp_path, monkeypatch):
    db = Database(tmp_path / "tuppence.db")
    first = mig.available_migrations()[:1]
    monkeypatch.setattr(mig, "available_migrations", lambda: first)
    mig.migrate(db, tmp_path / "backups")
    extra = ("9999_extra", "CREATE TABLE extra (x INTEGER);")
    monkeypatch.setattr(mig, "available_migrations", lambda: [*first, extra])
    assert mig.migrate(db, tmp_path / "backups") == ["9999_extra"]
    backups = list((tmp_path / "backups").iterdir())
    assert len(backups) == 1 and "pre-9999_extra" in backups[0].name


def test_failed_migration_rolls_back_and_raises(tmp_path, monkeypatch):
    db = Database(tmp_path / "tuppence.db")
    bad = ("0001_bad", "CREATE TABLE ok (x INTEGER); CREATE TABLE ok (x INTEGER);")
    monkeypatch.setattr(mig, "available_migrations", lambda: [bad])
    with pytest.raises(mig.MigrationError) as exc:
        mig.migrate(db, tmp_path / "backups")
    assert "0001_bad" in str(exc.value)
    assert "ok" not in tables(db)
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0


def test_migration_file_names_are_well_formed():
    for version, sql in mig.available_migrations():
        assert mig.VERSION_RE.match(version), version
        assert sql.strip()


def test_schema_constraints(tmp_path):
    db = Database(tmp_path / "tuppence.db")
    mig.migrate(db, tmp_path / "b")
    with db.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO person (id, display_name, role, created_at, updated_at)"
                " VALUES ('p1','  ','adult','x','x')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO profile_entry (subject_type, subject_id, attribute, value,"
                " valid_from, valid_to, created_at)"
                " VALUES ('person','p1','employment_status','\"employed\"',"
                "'2026-05-01','2026-04-01','x')"
            )


def test_failed_migration_rerun_keeps_one_pre_backup(tmp_path, monkeypatch):
    # A deterministic failure under a restart policy must not copy the database every start.
    from datetime import UTC, datetime, timedelta

    from tuppence.core import backup

    db = Database(tmp_path / "tuppence.db")
    first = mig.available_migrations()[:1]
    monkeypatch.setattr(mig, "available_migrations", lambda: first)
    mig.migrate(db, tmp_path / "backups")
    bad = ("9999_bad", "CREATE TABLE twice (x INTEGER); CREATE TABLE twice (x INTEGER);")
    monkeypatch.setattr(mig, "available_migrations", lambda: [*first, bad])
    stamps = iter(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=i) for i in range(10))
    monkeypatch.setattr(backup, "utcnow", lambda: next(stamps))
    for _ in range(3):
        with pytest.raises(mig.MigrationError):
            mig.migrate(db, tmp_path / "backups")
    assert len(list((tmp_path / "backups").glob("tuppence-pre-9999_bad-*.db"))) == 1
    # A different pending version still gets its own backup.
    monkeypatch.setattr(mig, "available_migrations", lambda: [*first, ("9998_ok", "SELECT 1;")])
    assert mig.migrate(db, tmp_path / "backups") == ["9998_ok"]
    assert len(list((tmp_path / "backups").glob("tuppence-pre-9998_ok-*.db"))) == 1


def test_refuses_data_from_a_newer_version(tmp_path):
    db = Database(tmp_path / "tuppence.db")
    mig.migrate(db, tmp_path / "backups")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES ('9999_future', 'x')"
        )
    with pytest.raises(mig.MigrationError, match="newer version of Tuppence"):
        mig.migrate(db, tmp_path / "backups")
    assert not any((tmp_path / "backups").glob("*.db"))


def test_0007_database_with_data_upgrades_to_0008(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    upto = [m for m in mig.available_migrations() if m[0] < "0008"]
    monkeypatch.setattr(mig, "available_migrations", lambda: upto)
    mig.migrate(db, tmp_path / "b")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO profile_entry (subject_type, subject_id, attribute, value, valid_from,"
            " created_at) VALUES ('household', '1', 'nation', '\"england\"', '2020-01-01', 'x')"
        )
    monkeypatch.undo()
    assert mig.migrate(db, tmp_path / "b") == ["0008_finance_profile"]
    with db.connection() as conn:
        row = conn.execute("SELECT value, version FROM profile_entry").fetchone()
        assert (row["value"], row["version"]) == ('"england"', 1)
        assert conn.execute("SELECT count(*) FROM account").fetchone()[0] == 0
