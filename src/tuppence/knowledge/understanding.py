"""Understanding rows: one per transaction, versioned, with a history (spec §7, §10.2).

Every write goes through `apply()`, which enforces the order of authority, writes a
history row for every real change and stamps the current knowledge version. Only the
person's actions (`set_by_person`, `release_by_person`) may change a confirmed row.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from typing import Any

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, VersionConflict
from tuppence.knowledge.authority import HUMAN, may_replace
from tuppence.knowledge.models import Decision, HistoryEntry, Understanding, Waiting
from tuppence.knowledge.versions import KnowledgeVersions

_FIELDS = (
    "merchant_id",
    "category_id",
    "who",
    "is_transfer",
    "transfer_pair_id",
    "ignored",
    "status",
    "confidence",
    "decided_by",
    "authority",
    "rule_id",
)


def _row(row: sqlite3.Row) -> Understanding:
    data = dict(row)
    data["evidence"] = json.loads(data["evidence"] or "{}")
    data["is_transfer"] = bool(data["is_transfer"])
    data["ignored"] = bool(data["ignored"])
    return Understanding.model_validate(data)


def _same(a: Understanding, b: Understanding) -> bool:
    for name in _FIELDS:
        left, right = getattr(a, name), getattr(b, name)
        if name == "confidence":
            if round(left, 3) != round(right, 3):
                return False
        elif left != right:
            return False
    return True


TRANSFER_CATEGORY = "transfers.between-accounts"


def _kind(conn: sqlite3.Connection, category_id: str | None) -> str | None:
    if category_id is None:
        return None
    row = conn.execute(
        "SELECT kind FROM category WHERE id = ? AND retired = 0", [category_id]
    ).fetchone()
    return None if row is None else str(row["kind"])


def merged(current: Understanding, decision: Decision) -> Understanding:
    """`current` with the decision's fields applied (None keeps the current value)."""
    update: dict[str, Any] = {
        "status": decision.status,
        "confidence": round(decision.confidence, 4),
        "decided_by": decision.decided_by,
        "authority": decision.authority,
        "rule_id": decision.rule_id,
        "evidence": decision.evidence,
    }
    for name in ("category_id", "who", "merchant_id", "is_transfer", "transfer_pair_id", "ignored"):
        value = getattr(decision, name)
        if value is not None:
            update[name] = value
    if decision.is_transfer is False:
        update["transfer_pair_id"] = None
    return current.model_copy(update=update)


class UnderstandingStore:
    def __init__(
        self, db: Database, versions: KnowledgeVersions, *, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self.db, self.versions, self.clock = db, versions, clock

    # --- reading -------------------------------------------------------------------------

    def get(self, transaction_id: str) -> Understanding:
        with self.db.connection() as conn:
            return self.get_in(conn, transaction_id)

    @staticmethod
    def get_in(conn: sqlite3.Connection, transaction_id: str) -> Understanding:
        row = conn.execute(
            "SELECT * FROM understanding WHERE transaction_id = ?", [transaction_id]
        ).fetchone()
        if row is None:
            raise NotFound("understanding", transaction_id)
        return _row(row)

    @staticmethod
    def many_in(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, Understanding]:
        rows = conn.execute(
            "SELECT * FROM understanding WHERE transaction_id IN (SELECT value FROM json_each(?))",
            [json.dumps(list(ids))],
        )
        return {row["transaction_id"]: _row(row) for row in rows}

    def history(self, transaction_id: str, *, limit: int = 20) -> list[HistoryEntry]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM understanding_history WHERE transaction_id = ?"
                " ORDER BY id DESC LIMIT ?",
                [transaction_id, limit],
            ).fetchall()
        out: list[HistoryEntry] = []
        for r in rows:
            data = dict(r)
            data["evidence"] = json.loads(data["evidence"])
            data["is_transfer"] = bool(data["is_transfer"])
            data["ignored"] = bool(data["ignored"])
            out.append(HistoryEntry.model_validate(data))
        return out

    # --- writing -------------------------------------------------------------------------

    def _write(
        self,
        conn: sqlite3.Connection,
        before: Understanding,
        after: Understanding,
        *,
        actor: str,
        run_id: str | None,
        reason: str,
        knowledge_version: int,
    ) -> None:
        now = to_iso(self.clock())
        conn.execute(
            "UPDATE understanding SET merchant_id = ?, category_id = ?, who = ?, is_transfer = ?,"
            " transfer_pair_id = ?, ignored = ?, status = ?, confidence = ?, decided_by = ?,"
            " authority = ?, rule_id = ?, evidence = ?, knowledge_version = ?, reviewed_at = ?,"
            " waiting = NULL, version = version + 1, updated_at = ? WHERE transaction_id = ?",
            [
                after.merchant_id,
                after.category_id,
                after.who,
                int(after.is_transfer),
                after.transfer_pair_id,
                int(after.ignored),
                after.status,
                after.confidence,
                after.decided_by,
                after.authority,
                after.rule_id,
                json.dumps(after.evidence),
                knowledge_version,
                now,
                now,
                before.transaction_id,
            ],
        )
        conn.execute(
            "INSERT INTO understanding_history (transaction_id, row_version, merchant_id,"
            " category_id, who, is_transfer, transfer_pair_id, ignored, status, confidence,"
            " decided_by, authority, rule_id, evidence, knowledge_version, changed_by, run_id,"
            " reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                before.transaction_id,
                before.version + 1,
                after.merchant_id,
                after.category_id,
                after.who,
                int(after.is_transfer),
                after.transfer_pair_id,
                int(after.ignored),
                after.status,
                after.confidence,
                after.decided_by,
                after.authority,
                after.rule_id,
                json.dumps(after.evidence),
                knowledge_version,
                actor,
                run_id,
                reason[:300],
                now,
            ],
        )

    def apply(
        self,
        conn: sqlite3.Connection,
        transaction_id: str,
        decision: Decision,
        *,
        actor: str,
        knowledge_version: int,
        run_id: str | None = None,
        reason: str = "",
    ) -> bool:
        """Apply a specialist's decision inside the caller's transaction.

        Returns True when the row changed. A decision that may not replace the row (spec
        §10.2) is dropped. A decision that changes nothing only refreshes the row's
        knowledge version, so the row isn't sent to the model again."""
        if decision.decided_by == "human":
            raise ValueError("the person's changes go through set_by_person()")
        current = self.get_in(conn, transaction_id)
        if not may_replace(current, decision.decided_by, decision.authority):
            return False
        after = merged(current, decision)
        if _same(current, after):
            conn.execute(
                "UPDATE understanding SET knowledge_version = ?, reviewed_at = ?, waiting = NULL"
                " WHERE transaction_id = ? AND status != 'confirmed'",
                [knowledge_version, to_iso(self.clock()), transaction_id],
            )
            return False
        self._write(
            conn,
            current,
            after,
            actor=actor,
            run_id=run_id,
            reason=reason,
            knowledge_version=knowledge_version,
        )
        return True

    def release(
        self,
        conn: sqlite3.Connection,
        transaction_id: str,
        *,
        actor: str,
        reason: str,
        run_id: str | None = None,
    ) -> bool:
        """Forget an agent's decision (its basis has gone). Never touches a confirmed row."""
        current = self.get_in(conn, transaction_id)
        if current.status in ("confirmed", "unknown"):
            return False
        after = current.model_copy(
            update={
                "status": "unknown",
                "confidence": 0.0,
                "decided_by": None,
                "authority": 0,
                "rule_id": None,
                "is_transfer": False,
                "transfer_pair_id": None,
                "evidence": {"released": reason},
            }
        )
        self._write(
            conn,
            current,
            after,
            actor=actor,
            run_id=run_id,
            reason=reason,
            knowledge_version=self.versions.current_in(conn),
        )
        conn.execute(
            "UPDATE understanding SET waiting = 'queued' WHERE transaction_id = ?", [transaction_id]
        )
        return True

    def set_waiting(self, conn: sqlite3.Connection, ids: Iterable[str], waiting: Waiting) -> int:
        count = 0
        for transaction_id in ids:
            count += conn.execute(
                "UPDATE understanding SET waiting = ? WHERE transaction_id = ?"
                " AND status != 'confirmed'",
                [waiting, transaction_id],
            ).rowcount
        return count

    # --- the person's actions --------------------------------------------------------------

    def set_by_person(
        self,
        transaction_id: str,
        *,
        expected_version: int,
        category_id: str | None = None,
        who: str | None = None,
        is_transfer: bool | None = None,
        ignored: bool | None = None,
    ) -> Understanding:
        """The person says what this transaction is. It becomes confirmed, is written to the
        history and bumps the knowledge version for its merchant (spec §10.2)."""
        if category_id is None and who is None and is_transfer is None and ignored is None:
            raise InputError("Nothing to change.")
        with self.db.transaction() as conn:
            current = self.get_in(conn, transaction_id)
            if current.version != expected_version:
                raise VersionConflict(
                    "understanding", transaction_id, expected_version, current.version
                )
            if is_transfer is True and category_id is None:
                category_id = TRANSFER_CATEGORY
            chosen = category_id or current.category_id
            kind = _kind(conn, chosen)
            if category_id is not None and kind is None:
                raise InputError("Choose a category from the list.")
            if category_id is not None and is_transfer is None:
                is_transfer = kind == "transfer"
            if is_transfer is False and kind in (None, "transfer"):
                raise InputError("Choose what this payment is instead.")
            version = self.versions.bump(
                conn, "correction", merchant_id=current.merchant_id, note="changed by you"
            )
            decision = Decision(
                decided_by="human",
                authority=HUMAN,
                status="confirmed",
                confidence=1.0,
                category_id=category_id,
                who=who,
                is_transfer=is_transfer,
                ignored=ignored,
                evidence={
                    "by": "person",
                    "previous": {
                        "category_id": current.category_id,
                        "decided_by": current.decided_by,
                    },
                },
            )
            self._write(
                conn,
                current,
                merged(current, decision),
                actor="person",
                run_id=None,
                reason="changed by you",
                knowledge_version=version,
            )
            if current.transfer_pair_id and is_transfer is False:
                self.release(
                    conn,
                    current.transfer_pair_id,
                    actor="person",
                    reason="its pair was marked as not a transfer",
                )
            return self.get_in(conn, transaction_id)

    def release_by_person(self, transaction_id: str, *, expected_version: int) -> Understanding:
        """ "Let Tuppence decide again": the row goes back to unknown and is queued."""
        with self.db.transaction() as conn:
            current = self.get_in(conn, transaction_id)
            if current.version != expected_version:
                raise VersionConflict(
                    "understanding", transaction_id, expected_version, current.version
                )
            after = current.model_copy(
                update={
                    "status": "unknown",
                    "confidence": 0.0,
                    "decided_by": None,
                    "authority": 0,
                    "rule_id": None,
                    "evidence": {"released_by": "person"},
                }
            )
            self._write(
                conn,
                current,
                after,
                actor="person",
                run_id=None,
                reason="you asked Tuppence to decide again",
                knowledge_version=self.versions.current_in(conn),
            )
            conn.execute(
                "UPDATE understanding SET waiting = 'queued' WHERE transaction_id = ?",
                [transaction_id],
            )
            return self.get_in(conn, transaction_id)
