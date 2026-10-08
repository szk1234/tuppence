"""Commitments: bills, subscriptions and instalments (spec §7), as the Commitments
specialist last found them. The person can say one isn't a commitment; that sticks for
that merchant and account, however often the payments carry on."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.records import NotFound, update_versioned
from tuppence.knowledge.cadence import Cadence, step

Kind = Literal["bill", "subscription", "instalment"]
Flag = Literal["price_rise", "missed", "lapsed", "duplicate", "free_trial_converted", "varies"]


class Detected(BaseModel):
    """One commitment as the specialist found it in this run."""

    merchant_id: str
    account_id: str
    category_id: str | None
    name: str
    kind: Kind
    kind_source: Literal["code", "llm", "user"]
    cadence: Cadence
    expected_amount_pence: int
    expected_day: int | None
    skip_months: list[int]
    first_date: date
    last_date: date
    next_due: date
    annual_cost_pence: int
    payment_ids: list[str]
    price_history: list[dict[str, Any]]
    flags: list[Flag] = Field(default_factory=list)
    status: Literal["active", "lapsed", "ended"]
    evidence: dict[str, Any] = Field(default_factory=dict)


class Commitment(BaseModel):
    id: str
    merchant_id: str
    account_id: str
    category_id: str | None
    name: str
    kind: Kind
    kind_source: str
    cadence: Cadence
    expected_amount_pence: int
    expected_day: int | None
    skip_months: list[int]
    first_date: date
    last_date: date
    next_due: date | None
    annual_cost_pence: int
    occurrences: int
    price_history: list[dict[str, Any]]
    flags: list[str]
    status: Literal["active", "lapsed", "ended"]
    dismissed: bool
    evidence: dict[str, Any]
    version: int


def _commitment(row: sqlite3.Row) -> Commitment:
    data = dict(row)
    for key in ("skip_months", "price_history", "flags", "evidence"):
        data[key] = json.loads(data[key])
    data["dismissed"] = bool(data["dismissed"])
    return Commitment.model_validate(data)


def project(c: Commitment, start: date, end: date) -> list[date]:
    """Due dates between `start` and `end` inclusive, stepping on from the next due date."""
    if c.next_due is None or c.status != "active":
        return []
    out: list[date] = []
    due = c.next_due
    while due <= end and len(out) < 400:
        if due >= start:
            out.append(due)
        due = step(due, c.cadence, c.expected_day, c.skip_months)
    return out


class CommitmentStore:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.db, self.clock = db, clock

    def list(self, *, include_dismissed: bool = False) -> list[Commitment]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM commitment WHERE dismissed = 0 OR ?"
                " ORDER BY status, annual_cost_pence DESC, name",
                [int(include_dismissed)],
            )
            return [_commitment(r) for r in rows]

    def get(self, commitment_id: str) -> Commitment:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM commitment WHERE id = ?", [commitment_id]).fetchone()
        if row is None:
            raise NotFound("commitment", commitment_id)
        return _commitment(row)

    def payment_ids(self, commitment_id: str) -> list[str]:
        with self.db.connection() as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT p.transaction_id FROM commitment_payment p"
                    ' JOIN "transaction" t ON t.id = p.transaction_id'
                    " WHERE p.commitment_id = ? ORDER BY t.date",
                    [commitment_id],
                )
            ]

    @staticmethod
    def dismissed_pairs(conn: sqlite3.Connection) -> set[tuple[str, str]]:
        return {
            (r[0], r[1])
            for r in conn.execute(
                "SELECT merchant_id, account_id FROM commitment WHERE dismissed = 1"
            )
        }

    def sync(self, conn: sqlite3.Connection, detected: Sequence[Detected]) -> dict[str, int]:
        """Make the stored commitments match this run's findings, inside the caller's
        transaction. A finding that shares payments with a stored commitment updates it
        (same id); the rest are new; stored ones no longer found are removed."""
        now = to_iso(self.clock())
        existing: dict[str, tuple[set[str], list[str]]] = {}
        for row in conn.execute("SELECT id, flags FROM commitment WHERE dismissed = 0"):
            ids = {
                r[0]
                for r in conn.execute(
                    "SELECT transaction_id FROM commitment_payment WHERE commitment_id = ?",
                    [row["id"]],
                )
            }
            existing[row["id"]] = (ids, json.loads(row["flags"]))
        counts = {"new": 0, "updated": 0, "removed": 0, "new_price_rises": 0}
        matched: set[str] = set()
        for found in detected:
            payments = set(found.payment_ids)
            best = max(
                (cid for cid in existing if cid not in matched and existing[cid][0] & payments),
                key=lambda cid: len(existing[cid][0] & payments),
                default=None,
            )
            fields = [
                found.merchant_id,
                found.account_id,
                found.category_id,
                found.name,
                found.kind,
                found.kind_source,
                found.cadence,
                found.expected_amount_pence,
                found.expected_day,
                json.dumps(found.skip_months),
                found.first_date.isoformat(),
                found.last_date.isoformat(),
                found.next_due.isoformat(),
                found.annual_cost_pence,
                len(found.payment_ids),
                json.dumps(found.price_history),
                json.dumps(found.flags),
                found.status,
                json.dumps(found.evidence),
            ]
            if best is None:
                commitment_id = "c_" + secrets.token_hex(6)
                conn.execute(
                    "INSERT INTO commitment (merchant_id, account_id, category_id, name, kind,"
                    " kind_source, cadence, expected_amount_pence, expected_day, skip_months,"
                    " first_date, last_date, next_due, annual_cost_pence, occurrences,"
                    " price_history, flags, status, evidence, id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [*fields, commitment_id, now, now],
                )
                counts["new"] += 1
                was_flags: list[str] = []
            else:
                commitment_id = best
                matched.add(best)
                was_flags = existing[best][1]
                conn.execute(
                    "UPDATE commitment SET merchant_id = ?, account_id = ?, category_id = ?,"
                    " name = ?, kind = ?, kind_source = ?, cadence = ?, expected_amount_pence = ?,"
                    " expected_day = ?, skip_months = ?, first_date = ?, last_date = ?,"
                    " next_due = ?, annual_cost_pence = ?, occurrences = ?, price_history = ?,"
                    " flags = ?, status = ?, evidence = ?, version = version + 1,"
                    " updated_at = ? WHERE id = ?",
                    [*fields, now, best],
                )
                conn.execute("DELETE FROM commitment_payment WHERE commitment_id = ?", [best])
                counts["updated"] += 1
            if "price_rise" in found.flags and "price_rise" not in was_flags:
                counts["new_price_rises"] += 1
            conn.executemany(
                "INSERT OR IGNORE INTO commitment_payment (commitment_id, transaction_id)"
                " VALUES (?, ?)",
                [(commitment_id, t) for t in found.payment_ids],
            )
        for cid in existing:
            if cid not in matched:
                conn.execute("DELETE FROM commitment WHERE id = ?", [cid])
                counts["removed"] += 1
        return counts

    def set_dismissed(
        self, commitment_id: str, expected_version: int, dismissed: bool
    ) -> Commitment:
        """The person says this isn't (or is after all) a commitment."""
        with self.db.transaction() as conn:
            update_versioned(
                conn,
                "commitment",
                "id",
                commitment_id,
                expected_version,
                {"dismissed": int(dismissed)},
                now=to_iso(self.clock()),
            )
        return self.get(commitment_id)
