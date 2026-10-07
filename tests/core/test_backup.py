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
