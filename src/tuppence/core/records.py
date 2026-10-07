"""Per-record writes with optimistic version checks (spec §3.4)."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class VersionConflict(Exception):
    def __init__(self, table: str, key: object, expected: int, current: int) -> None:
        super().__init__(f"{table} {key!r}: expected version {expected}, found {current}")
        self.table, self.key, self.expected, self.current = table, key, expected, current


class NotFound(Exception):
    def __init__(self, table: str, key: object) -> None:
        super().__init__(f"{table} {key!r} not found")
        self.table, self.key = table, key


def _check(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def update_versioned(
    conn: sqlite3.Connection,
    table: str,
    key_col: str,
    key: object,
    expected_version: int,
    changes: Mapping[str, object],
    *,
    now: str,
) -> int:
    _check(table)
    _check(key_col)
    if not changes:
        raise ValueError("no changes given")
    assignments = ", ".join(f"{_check(col)} = ?" for col in changes)
    sql = (
        f"UPDATE {table} SET {assignments}, version = version + 1, updated_at = ? "  # noqa: S608
        f"WHERE {key_col} = ? AND version = ?"
    )
    cur = conn.execute(sql, [*changes.values(), now, key, expected_version])
    if cur.rowcount == 1:
        return expected_version + 1
    row = conn.execute(f"SELECT version FROM {table} WHERE {key_col} = ?", [key]).fetchone()  # noqa: S608
    if row is None:
        raise NotFound(table, key)
    raise VersionConflict(table, key, expected_version, int(row[0]))
