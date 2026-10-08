"""The log of sub-categories the Categoriser added, and how to undo them (spec §8.2:
"proposes sub-categories and re-files existing records into them (logged, undoable)")."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import datetime

from pydantic import BaseModel

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound
from tuppence.knowledge.models import Decision, Understanding
from tuppence.knowledge.understanding import UnderstandingStore
from tuppence.knowledge.versions import KnowledgeVersions


class Refile(BaseModel):
    id: str
    run_id: str | None
    parent_id: str
    created_ids: list[str]
    moves: list[dict[str, str]]  # {"transaction_id", "to"}
    undone_at: str | None
    created_at: str


def _refile(row: sqlite3.Row) -> Refile:
    data = dict(row)
    data["created_ids"] = json.loads(data["created_ids"])
    data["moves"] = json.loads(data["moves"])
    return Refile.model_validate(data)


def refile_decision(current: Understanding, to: str) -> Decision:
    """The same decision as before, filed under another category (deeper, or back up)."""
    return Decision(
        decided_by=current.decided_by or "llm",
        authority=current.authority,
        status=current.status,
        confidence=current.confidence,
        category_id=to,
        who=current.who,
        evidence={**current.evidence, "refiled_from": current.category_id},
    )


class RefileStore:
    def __init__(
        self,
        db: Database,
        versions: KnowledgeVersions,
        understanding: UnderstandingStore,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db, self.versions, self.understanding, self.clock = db, versions, understanding, clock

    @staticmethod
    def considered(conn: sqlite3.Connection, parent_id: str) -> bool:
        """True once a category has been looked at for splitting (even if nothing came of it,
        or the person undid it): Tuppence doesn't ask again."""
        return (
            conn.execute(
                "SELECT 1 FROM category_refile WHERE parent_id = ?", [parent_id]
            ).fetchone()
            is not None
        )

    def record(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str | None,
        parent_id: str,
        created_ids: list[str],
        moves: list[dict[str, str]],
    ) -> str:
        refile_id = "rf_" + secrets.token_hex(5)
        conn.execute(
            "INSERT INTO category_refile (id, run_id, parent_id, created_ids, moves, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                refile_id,
                run_id,
                parent_id,
                json.dumps(created_ids),
                json.dumps(moves),
                to_iso(self.clock()),
            ],
        )
        return refile_id

    def list(self, *, include_undone: bool = False) -> list[Refile]:
        sql = "SELECT * FROM category_refile WHERE created_ids != '[]'"
        if not include_undone:
            sql += " AND undone_at IS NULL"
        with self.db.connection() as conn:
            return [_refile(r) for r in conn.execute(sql + " ORDER BY created_at DESC")]

    def undo(self, refile_id: str) -> int:
        """Put the moved transactions back and retire the new sub-categories (the person's
        action). Rows changed since (by the person or a rule) are left alone, rows removed
        since (with their statement) are skipped, and a new sub-category that still holds any
        of them stays. Returns how many rows moved back."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM category_refile WHERE id = ?", [refile_id]).fetchone()
            if row is None:
                raise NotFound("category_refile", refile_id)
            refile = _refile(row)
            if refile.undone_at is not None:
                raise InputError("This has already been undone.")
            version = self.versions.bump(
                conn, "category", category_id=refile.parent_id, note="sub-categories undone"
            )
            moved = 0
            rows = self.understanding.many_in(conn, [m["transaction_id"] for m in refile.moves])
            for move in refile.moves:
                current = rows.get(move["transaction_id"])
                if current is None:  # removed since, with its statement
                    continue
                if current.category_id != move["to"] or current.decided_by not in (
                    "llm",
                    "review",
                    "memory",
                ):
                    continue
                decision = refile_decision(current, refile.parent_id)
                moved += self.understanding.apply(
                    conn,
                    move["transaction_id"],
                    decision,
                    actor="person",
                    knowledge_version=version,
                    reason="sub-categories undone",
                )
            for cid in refile.created_ids:
                conn.execute(
                    "UPDATE merchant SET default_category_id = ? WHERE default_category_id = ?"
                    " AND memory = 'inferred'",
                    [refile.parent_id, cid],
                )
                still_used = conn.execute(
                    "SELECT 1 FROM understanding WHERE category_id = ? LIMIT 1", [cid]
                ).fetchone()
                if still_used is None:
                    conn.execute(
                        "UPDATE category SET retired = 1, version = version + 1 WHERE id = ?", [cid]
                    )
            conn.execute(
                "UPDATE category_refile SET undone_at = ? WHERE id = ?",
                [to_iso(self.clock()), refile_id],
            )
        return moved
