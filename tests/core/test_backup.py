from datetime import date

from tuppence.core.backup import backup_db, daily_backup, rotate_backups
from tuppence.core.db import Database


def make_db(tmp_path):
    db = Database(tmp_path / "tuppence.db")
    with db.connection() as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('hello')")
    return db


def test_backup_is_a_readable_copy(tmp_path):
    db = make_db(tmp_path)
    out = backup_db(db.path, tmp_path / "backups", "manual")
    copy = Database(out)
    with copy.connection() as conn:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "hello"
    assert out.name.startswith("tuppence-manual-")


def test_daily_backup_once_per_day_and_rotation(tmp_path):
    db = make_db(tmp_path)
    dest = tmp_path / "backups"
    for day in range(1, 10):
        daily_backup(db.path, dest, date(2026, 10, day))
    assert daily_backup(db.path, dest, date(2026, 10, 9)) is None  # already done today
    names = sorted(p.name for p in dest.iterdir())
    assert names == [f"daily-2026-10-0{d}.db" for d in range(3, 10)]


def test_rotate_ignores_other_files(tmp_path):
    dest = tmp_path / "b"
    dest.mkdir()
    (dest / "tuppence-pre-0002.db").write_text("x")
    for d in range(1, 4):
        (dest / f"daily-2026-01-0{d}.db").write_text("x")
    removed = rotate_backups(dest, keep=2)
    assert [p.name for p in removed] == ["daily-2026-01-01.db"]
    assert (dest / "tuppence-pre-0002.db").exists()


def test_two_copies_at_once_never_share_a_temporary_file(tmp_path, monkeypatch):
    """Two daily backups started together (the start-up job and a recovered one) each copy to
    their own temporary file: with one fixed name, one copy wrote into or moved away the
    other's file."""
    import sqlite3

    from tuppence.core import backup

    db = make_db(tmp_path)
    targets = []
    real_connect = sqlite3.connect

    def spy(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            targets.append(str(path))
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(backup.sqlite3, "connect", spy)
    dest = tmp_path / "backups"
    dest.mkdir()
    backup._copy(db.path, dest / "daily-2026-10-08.db")
    backup._copy(db.path, dest / "daily-2026-10-08.db")
    assert len(targets) == 2 and targets[0] != targets[1]
    assert sorted(p.name for p in dest.iterdir()) == ["daily-2026-10-08.db"]  # no leftovers


def test_concurrent_daily_backups_all_succeed(tmp_path):
    import threading

    db = make_db(tmp_path)
    dest = tmp_path / "backups"
    dest.mkdir()
    for day in range(1, 8):  # a full week: each backup also removes the oldest
        (dest / f"daily-2026-10-0{day}.db").write_text("x")
    barrier = threading.Barrier(6)
    errors = []

    def run():
        barrier.wait()
        try:
            daily_backup(db.path, dest, date(2026, 10, 8))
        except Exception as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    workers = [threading.Thread(target=run) for _ in range(6)]
    for t in workers:
        t.start()
    for t in workers:
        t.join(30)
    assert errors == []
    assert sorted(p.name for p in dest.iterdir()) == [
        f"daily-2026-10-0{day}.db" for day in range(2, 9)
    ]
    with Database(dest / "daily-2026-10-08.db").connection() as conn:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "hello"


def test_a_failed_copy_leaves_no_temporary_file(tmp_path, monkeypatch):
    import sqlite3

    import pytest

    from tuppence.core import backup

    db = make_db(tmp_path)
    dest = tmp_path / "backups"
    dest.mkdir()

    class Broken:
        def __init__(self, conn):
            self.conn = conn

        def backup(self, target):
            raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            self.conn.close()

    real_connect = sqlite3.connect

    def connect(path, *args, **kwargs):
        conn = real_connect(path, *args, **kwargs)
        return conn if str(path).endswith(".tmp") else Broken(conn)

    monkeypatch.setattr(backup.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError):
        backup._copy(db.path, dest / "daily-2026-10-08.db")
    assert list(dest.iterdir()) == []
