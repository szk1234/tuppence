"""Numbered SQL migrations shipped inside the package (spec §14.1)."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from tuppence.core.backup import backup_db
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database

PACKAGE = "tuppence.core.migrations"
VERSION_RE = re.compile(r"^\d{4}_[a-z0-9_]+$")


class MigrationError(RuntimeError):
    pass


def available_migrations() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for entry in resources.files(PACKAGE).iterdir():
        name = entry.name
        if name.endswith(".sql"):
            version = name[:-4]
            if not VERSION_RE.match(version):
                raise MigrationError(f"Badly named migration file: {name}")
            found.append((version, entry.read_text(encoding="utf-8")))
    return sorted(found)


def migrate(db: Database, backups_dir: Path) -> list[str]:
    with db.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations"
            " (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    todo = [(v, sql) for v, sql in available_migrations() if v not in done]
    if not todo:
        return []
    if done:
        backup_db(db.path, backups_dir, f"pre-{todo[0][0]}")
    applied: list[str] = []
    for version, sql in todo:
        if not VERSION_RE.match(version):
            raise MigrationError(f"Badly named migration: {version}")
        # executescript takes no parameters; version is regex-validated and the
        # timestamp is generated locally, so inlining them is safe.
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{sql}\n;\n"
            "INSERT INTO schema_migrations (version, applied_at)"  # noqa: S608
            f" VALUES ('{version}', '{to_iso(utcnow())}');\n"
            "COMMIT;"
        )
        with db.connection() as conn:
            try:
                conn.executescript(script)
            except Exception as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise MigrationError(f"Migration {version} failed: {exc}") from exc
        applied.append(version)
    return applied
