"""Database backups: before migrations and once a day (spec §14.1)."""

from __future__ import annotations

import re
import secrets
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

from tuppence.core.clock import utcnow

# A backup's temporary file: `<backup name>.<12 hex>.tmp`, or `<backup name stem>.tmp` (the
# fixed name versions before 0.2 used).
TEMP_NAME = re.compile(r"(?:daily-|tuppence-)[A-Za-z0-9_.-]+\.tmp")
STALE_TEMP_S = 15 * 60  # untouched this long: a copy still running keeps writing its file


def backup_db(src: Path, dest_dir: Path, label: str, *, now: datetime | None = None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or utcnow()).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / f"tuppence-{label}-{stamp}.db"
    return _copy(src, dest)


def daily_backup(src: Path, dest_dir: Path, today: date, *, keep: int = 7) -> Path | None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"daily-{today.isoformat()}.db"
    if dest.exists():
        return None
    _copy(src, dest)
    rotate_backups(dest_dir, prefix="daily-", keep=keep)
    return dest


def rotate_backups(dest_dir: Path, *, prefix: str = "daily-", keep: int = 7) -> list[Path]:
    files = sorted(
        (p for p in dest_dir.glob(f"{prefix}*.db") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = files[keep:]
    for path in removed:  # a backup running at the same time may have removed it already
        path.unlink(missing_ok=True)
    return sorted(removed, key=lambda p: p.name)


def remove_stale_temps(dest_dir: Path, *, older_than_s: float = STALE_TEMP_S) -> list[Path]:
    """Remove the temporary files of backups a hard stop cut short (they are never finished
    or moved into place). Only Tuppence's own (`daily-…` or `tuppence-…`, ending `.tmp`), and
    only old ones: a recent one may be another backup still copying."""
    cutoff = time.time() - older_than_s
    removed: list[Path] = []
    for path in dest_dir.glob("*.tmp"):
        if not TEMP_NAME.fullmatch(path.name) or not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except FileNotFoundError:  # another backup removed it first
            continue
    return removed


def _copy(src: Path, dest: Path) -> Path:
    """Copy the database to `dest` through a temporary file of this copy's own, so two copies
    to the same place at once (two daily backup jobs) never write into or move away each
    other's file. Whichever finishes last leaves its complete copy at `dest`."""
    remove_stale_temps(dest.parent)
    tmp = dest.with_name(f"{dest.name}.{secrets.token_hex(6)}.tmp")
    try:
        source = sqlite3.connect(src)
        try:
            target = sqlite3.connect(tmp)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest
