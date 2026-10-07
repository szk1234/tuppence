"""Database backups: before migrations and once a day (spec §14.1)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from tuppence.core.clock import utcnow


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
    for path in removed:
        path.unlink()
    return sorted(removed, key=lambda p: p.name)


def _copy(src: Path, dest: Path) -> Path:
    tmp = dest.with_suffix(".tmp")
    source = sqlite3.connect(src)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    tmp.replace(dest)
    return dest
