"""Every outbound call, so users can see exactly what left their machine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tuppence.core.db import Database

Purpose = Literal["llm", "research", "market", "datapack"]


class PrivacyEvent(BaseModel):
    ts: str
    purpose: Purpose
    task: str | None
    connection_id: str | None
    destination: str
    method: str
    path: str
    bytes_out: int
    bytes_in: int
    status: int | None
    redactions: int
    outcome: Literal["sent", "blocked", "error"]
    note: str | None


class PrivacyLogEntry(PrivacyEvent):
    id: int


_COLS = list(PrivacyEvent.model_fields)


class PrivacyLog:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, event: PrivacyEvent) -> None:
        data = event.model_dump()
        cols = ", ".join(_COLS)
        marks = ", ".join("?" for _ in _COLS)
        with self.db.transaction() as conn:
            conn.execute(
                f"INSERT INTO privacy_log ({cols}) VALUES ({marks})",  # noqa: S608  (column names come from PrivacyEvent.model_fields, values are bound)
                [data[c] for c in _COLS],
            )

    def prune(self, before: str) -> int:
        """Delete entries logged before `before` (a UTC ISO time). Returns how many."""
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM privacy_log WHERE ts < ?", [before]).rowcount

    def list(self, limit: int = 100, before_id: int | None = None) -> list[PrivacyLogEntry]:
        sql = "SELECT * FROM privacy_log"
        params: list[int] = []
        if before_id is not None:
            sql += " WHERE id < ?"
            params.append(before_id)
        with self.db.connection() as conn:
            rows = conn.execute(
                sql + " ORDER BY id DESC LIMIT ?", [*params, max(1, min(limit, 500))]
            ).fetchall()
        return [PrivacyLogEntry(**{k: r[k] for k in PrivacyLogEntry.model_fields}) for r in rows]
