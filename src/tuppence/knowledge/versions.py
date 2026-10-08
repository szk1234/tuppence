"""The knowledge version: a counter bumped by every change to a rule, merchant or category
(spec §7). Each bump records what it touched, so the backlog sweep can tell which
understanding rows were decided before a change that concerns them."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.knowledge.models import VersionKind

# An understanding row is stale when a later version touched its merchant or its category.
STALE_SQL = (
    "EXISTS (SELECT 1 FROM knowledge_version kv WHERE kv.version > u.knowledge_version"
    " AND ((u.merchant_id IS NOT NULL AND kv.merchant_id = u.merchant_id)"
    " OR (u.category_id IS NOT NULL AND kv.category_id = u.category_id)))"
)


class KnowledgeVersions:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.clock = clock

    @staticmethod
    def current_in(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM knowledge_version").fetchone()
        return int(row[0])

    def current(self) -> int:
        with self.db.connection() as conn:
            return self.current_in(conn)

    def bump(
        self,
        conn: sqlite3.Connection,
        kind: VersionKind,
        *,
        merchant_id: str | None = None,
        category_id: str | None = None,
        rule_id: str | None = None,
        note: str = "",
    ) -> int:
        """Record a change inside the caller's transaction. Returns the new version."""
        cur = conn.execute(
            "INSERT INTO knowledge_version (kind, merchant_id, category_id, rule_id, note,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [kind, merchant_id, category_id, rule_id, note[:200], to_iso(self.clock())],
        )
        return int(cur.lastrowid or 0)
