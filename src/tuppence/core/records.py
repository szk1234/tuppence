"""Per-record writes with optimistic version checks (spec §3.4).

Status rule for user-owned records (ruling R-M2-8), the same for every entity:

* A status change to the status the record already has is refused with a plain InputError
  ("This account is already closed."), never a silent success that bumps the version.
* A record that isn't active (a closed account, a settled debt, an achieved or dropped goal)
  can't be edited: reopen it first. An ended income can't be reopened: add a new one instead.
"""

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
    key_col: str | tuple[str, ...],
    key: object,
    expected_version: int,
    changes: Mapping[str, object],
    *,
    now: str,
) -> int:
    """Bump `version` if it still equals `expected_version`.

    `key_col`/`key` may be tuples of the same length for composite keys.
    """
    _check(table)
    cols = (key_col,) if isinstance(key_col, str) else tuple(key_col)
    if isinstance(key_col, str):
        vals: tuple[object, ...] = (key,)
    elif isinstance(key, tuple):
        vals = key
    else:
        raise TypeError("a tuple of key columns needs a tuple of key values")
    if not cols or len(cols) != len(vals):
        raise ValueError("key columns and key values must match")
    where = " AND ".join(f"{_check(c)} = ?" for c in cols)
    if not changes:
        raise ValueError("no changes given")
    assignments = ", ".join(f"{_check(col)} = ?" for col in changes)
    sql = (
        f"UPDATE {table} SET {assignments}, version = version + 1, updated_at = ? "  # noqa: S608
        f"WHERE {where} AND version = ?"
    )
    cur = conn.execute(sql, [*changes.values(), now, *vals, expected_version])
    if cur.rowcount == 1:
        return expected_version + 1
    row = conn.execute(f"SELECT version FROM {table} WHERE {where}", list(vals)).fetchone()  # noqa: S608
    if row is None:
        raise NotFound(table, key)
    raise VersionConflict(table, key, expected_version, int(row[0]))
