"""Statements, transactions, balance history and layout memory in SQLite (spec §6.2, §7)."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.records import NotFound, VersionConflict
from tuppence.ingest.dedupe import Existing, assign_fingerprints, plan_dedupe
from tuppence.ingest.models import FileKind, ParsedStatement, StatementStatus

STATUS_LABELS: dict[str, str] = {
    "received": "Waiting to be read",
    "identifying": "Reading",
    "needs_account": "Which account is this?",
    "parsing": "Reading transactions",
    "needs_review": "Needs your check",
    "imported": "Imported",
    "failed": "Couldn't import",
}
IN_PROGRESS = ("received", "identifying", "parsing")
FINISHED = ("needs_review", "imported", "failed")
MEMORY_SIZE = 10  # answers kept per layout fingerprint
_JSON_FIELDS = {"question", "draft", "check_errors", "stats"}
_COLUMNS = {
    "account_id",
    "importer",
    "layout_fingerprint",
    "provider",
    "period_start",
    "period_end",
    "opening_balance_pence",
    "closing_balance_pence",
    "balance_verified",
    "status",
    "question",
    "draft",
    "check_errors",
    "stats",
    "error",
    "run",
    "analysis_state",
}


class StatementRecord(BaseModel):
    id: str
    account_id: str | None
    file_sha256: str
    file_ext: str
    original_filename: str
    format: FileKind
    importer: str | None
    layout_fingerprint: str | None
    provider: str | None
    period_start: date | None
    period_end: date | None
    opening_balance_pence: int | None
    closing_balance_pence: int | None
    balance_verified: bool
    status: StatementStatus
    question: dict[str, Any] | None
    draft: dict[str, Any] | None
    check_errors: list[str]
    stats: dict[str, Any]
    error: str | None
    run: int
    analysis_state: str
    version: int
    created_at: str
    updated_at: str


class TransactionRecord(BaseModel):
    id: str
    account_id: str
    statement_id: str
    date: date
    amount_pence: int
    raw_description: str
    merchant_text: str | None
    bank_category: str | None
    bank_type: str | None
    balance_after_pence: int | None
    source_ref: str


class PersistResult(BaseModel):
    rows: int = 0
    inserted: int = 0
    duplicates_exact: int = 0
    duplicates_similar: int = 0
    skipped: int = 0
    similar_to: dict[str, str] = Field(default_factory=dict)  # source ref → existing transaction id


def _record(row: sqlite3.Row) -> StatementRecord:
    data = dict(row)
    for key in _JSON_FIELDS:
        data[key] = json.loads(data[key]) if data[key] else None
    data["check_errors"] = data["check_errors"] or []
    data["stats"] = data["stats"] or {}
    data["balance_verified"] = bool(data["balance_verified"])
    return StatementRecord.model_validate(data)


def _encode(key: str, value: Any) -> Any:
    if key in _JSON_FIELDS:
        return None if value is None else json.dumps(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return int(value)
    return value


class StatementStore:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.clock = clock

    def create(self, *, sha256: str, ext: str, filename: str, kind: FileKind) -> StatementRecord:
        statement_id = "s_" + secrets.token_hex(5)
        now = to_iso(self.clock())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO statement (id, file_sha256, file_ext, original_filename, format,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [statement_id, sha256, ext, filename, kind, now, now],
            )
        return self.get(statement_id)

    def get(self, statement_id: str) -> StatementRecord:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM statement WHERE id = ?", [statement_id]).fetchone()
        if row is None:
            raise NotFound("statement", statement_id)
        return _record(row)

    def find_by_sha(self, sha256: str) -> StatementRecord | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM statement WHERE file_sha256 = ?", [sha256]).fetchone()
        return None if row is None else _record(row)

    def list(self, *, limit: int = 200) -> list[StatementRecord]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM statement ORDER BY created_at DESC, id DESC LIMIT ?", [limit]
            ).fetchall()
        return [_record(r) for r in rows]

    def unfinished(self) -> list[str]:
        """Statements a run was part-way through (not waiting for the person)."""
        marks = ", ".join("?" for _ in IN_PROGRESS)
        with self.db.connection() as conn:
            rows = conn.execute(
                f"SELECT id FROM statement WHERE status IN ({marks})",  # noqa: S608 - '?' only
                list(IN_PROGRESS),
            ).fetchall()
        return [r["id"] for r in rows]

    def _write(
        self,
        conn: sqlite3.Connection,
        statement_id: str,
        fields: dict[str, Any],
        expected_version: int | None,
    ) -> None:
        unknown = set(fields) - _COLUMNS
        if unknown:
            raise ValueError(f"not statement columns: {sorted(unknown)}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        params = [_encode(k, v) for k, v in fields.items()]
        # column names come from the _COLUMNS allow-list above
        sql = f"UPDATE statement SET {sets}, version = version + 1, updated_at = ? WHERE id = ?"  # noqa: S608
        params += [to_iso(self.clock()), statement_id]
        if expected_version is not None:
            sql += " AND version = ?"
            params.append(expected_version)
        if conn.execute(sql, params).rowcount == 1:
            return
        row = conn.execute("SELECT version FROM statement WHERE id = ?", [statement_id]).fetchone()
        if row is None:
            raise NotFound("statement", statement_id)
        raise VersionConflict("statement", statement_id, expected_version or 0, int(row["version"]))

    def update(self, statement_id: str, **fields: Any) -> StatementRecord:
        """A write by Tuppence itself (status changes and results)."""
        with self.db.transaction() as conn:
            self._write(conn, statement_id, fields, None)
        return self.get(statement_id)

    def update_versioned(
        self, statement_id: str, expected_version: int, **fields: Any
    ) -> StatementRecord:
        """A write on behalf of the person: a stale version raises VersionConflict (HTTP 409)."""
        with self.db.transaction() as conn:
            self._write(conn, statement_id, fields, expected_version)
        return self.get(statement_id)

    # --- layout memory -------------------------------------------------------------------------

    def remembered_accounts(self, fingerprint: str) -> list[str]:
        """Accounts earlier answers tied to this layout fingerprint, oldest first."""
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT account_ids FROM layout_memory WHERE fingerprint = ?", [fingerprint]
            ).fetchone()
        return [] if row is None else list(json.loads(row["account_ids"]))

    def remember_layout(self, fingerprint: str, account_id: str) -> None:
        with self.db.transaction() as conn:
            self._remember(conn, fingerprint, account_id)

    def _remember(self, conn: sqlite3.Connection, fingerprint: str, account_id: str) -> None:
        row = conn.execute(
            "SELECT account_ids FROM layout_memory WHERE fingerprint = ?", [fingerprint]
        ).fetchone()
        ids = [] if row is None else [i for i in json.loads(row["account_ids"]) if i != account_id]
        ids = [*ids, account_id][-MEMORY_SIZE:]
        conn.execute(
            "INSERT INTO layout_memory (fingerprint, account_ids, times_seen, updated_at)"
            " VALUES (?, ?, 1, ?)"
            " ON CONFLICT(fingerprint) DO UPDATE SET account_ids = excluded.account_ids,"
            " times_seen = layout_memory.times_seen + 1, updated_at = excluded.updated_at",
            [fingerprint, json.dumps(ids), to_iso(self.clock())],
        )

    # --- importing ---------------------------------------------------------------------------

    def persist(
        self,
        statement_id: str,
        account_id: str,
        parsed: ParsedStatement,
        *,
        balance_verified: bool,
        stats: dict[str, Any],
        expected_version: int | None = None,
    ) -> PersistResult:
        """Import a statement in one transaction: every new row, the closing balance, the
        account answer for its layout fingerprint, and the statement's result. Nothing is kept
        if any part fails.

        Rows already stored for the account from another statement are not stored again: the
        same fingerprint, or the same transaction seen in another format (`plan_dedupe`).
        `expected_version` is given when the person imports from the fix-up screen.
        Safe to call twice: an already-imported statement returns its stored result.
        """
        now = to_iso(self.clock())
        with self.db.transaction() as conn:
            current = conn.execute(
                "SELECT status, stats, version, layout_fingerprint FROM statement WHERE id = ?",
                [statement_id],
            ).fetchone()
            if current is None:
                raise NotFound("statement", statement_id)
            if current["status"] == "imported":
                return PersistResult.model_validate(json.loads(current["stats"]).get("persist", {}))
            if expected_version is not None and current["version"] != expected_version:
                raise VersionConflict(
                    "statement", statement_id, expected_version, int(current["version"])
                )
            dates = [r.date for r in parsed.rows]
            lo = parsed.period_start or (min(dates) if dates else None)
            hi = parsed.period_end or (max(dates) if dates else None)
            existing: list[Existing] = []
            if lo is not None and hi is not None:
                existing = [
                    Existing(
                        r["id"],
                        date.fromisoformat(r["date"]),
                        r["amount_pence"],
                        r["raw_description"],
                        r["fingerprint"],
                    )
                    for r in conn.execute(
                        "SELECT id, date, amount_pence, raw_description, fingerprint"
                        ' FROM "transaction"'
                        " WHERE account_id = ? AND statement_id != ? AND date BETWEEN ? AND ?",
                        [
                            account_id,
                            statement_id,
                            (lo - timedelta(days=3)).isoformat(),
                            (hi + timedelta(days=3)).isoformat(),
                        ],
                    )
                ]
            fingerprints = assign_fingerprints(parsed.rows, account_id)
            plan = plan_dedupe(
                parsed.rows,
                [fp for fp, _ in fingerprints],
                existing,
                window=(lo or date.min, hi or date.max),
            )
            inserted = 0
            for i in plan.insert:
                row, (fp, occurrence) = parsed.rows[i], fingerprints[i]
                cur = conn.execute(
                    'INSERT INTO "transaction" (id, account_id, statement_id, date, amount_pence,'
                    " currency, raw_description, merchant_text, bank_category, bank_type,"
                    " balance_after_pence, source_ref, fingerprint, occurrence,"
                    " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(account_id, fingerprint) DO NOTHING",
                    [
                        "t_" + secrets.token_hex(8),
                        account_id,
                        statement_id,
                        row.date.isoformat(),
                        row.amount_pence,
                        parsed.currency,
                        row.raw_description,
                        row.merchant,
                        row.bank_category,
                        row.bank_type,
                        row.balance_after_pence,
                        row.ref,
                        fp,
                        occurrence,
                        now,
                    ],
                )
                inserted += cur.rowcount
            if parsed.closing_balance_pence is not None and hi is not None:
                household = (
                    -parsed.closing_balance_pence
                    if parsed.perspective == "card"
                    else parsed.closing_balance_pence
                )
                conn.execute(
                    "INSERT INTO account_balance (account_id, as_of, balance_pence, source,"
                    " statement_id, created_at) VALUES (?, ?, ?, 'statement', ?, ?)"
                    " ON CONFLICT(account_id, as_of, statement_id)"
                    " DO UPDATE SET balance_pence = excluded.balance_pence",
                    [account_id, hi.isoformat(), household, statement_id, now],
                )
            if current["layout_fingerprint"]:
                self._remember(conn, current["layout_fingerprint"], account_id)
            result = PersistResult(
                rows=len(parsed.rows),
                inserted=inserted,
                duplicates_exact=len(parsed.rows) - inserted - len(plan.similar),
                duplicates_similar=len(plan.similar),
                skipped=len(parsed.skipped),
                similar_to={parsed.rows[i].ref: tid for i, tid in plan.similar.items()},
            )
            self._write(
                conn,
                statement_id,
                {
                    "status": "imported",
                    "account_id": account_id,
                    "importer": parsed.importer,
                    "period_start": lo,
                    "period_end": hi,
                    "opening_balance_pence": parsed.opening_balance_pence,
                    "closing_balance_pence": parsed.closing_balance_pence,
                    "balance_verified": balance_verified,
                    "question": None,
                    "draft": None,
                    "check_errors": [],
                    "error": None,
                    "analysis_state": "pending",
                    "stats": {**stats, "persist": result.model_dump()},
                },
                None,
            )
        return result

    def transactions(self, statement_id: str, *, limit: int = 500) -> list[TransactionRecord]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT id, account_id, statement_id, date, amount_pence, raw_description,"
                " merchant_text, bank_category, bank_type, balance_after_pence, source_ref"
                ' FROM "transaction" WHERE statement_id = ?'
                " ORDER BY date, rowid LIMIT ?",
                [statement_id, limit],
            ).fetchall()
        return [TransactionRecord.model_validate(dict(r)) for r in rows]

    # --- balances ------------------------------------------------------------------------------

    def set_manual_balance(self, account_id: str, as_of: date, balance_pence: int) -> None:
        """A balance the person typed in. One per account and day: a second entry for the same
        day replaces the first (the table's UNIQUE can't do it: its statement_id is NULL)."""
        now = to_iso(self.clock())
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM account_balance"
                " WHERE account_id = ? AND as_of = ? AND statement_id IS NULL",
                [account_id, as_of.isoformat()],
            )
            conn.execute(
                "INSERT INTO account_balance (account_id, as_of, balance_pence, source,"
                " statement_id, created_at) VALUES (?, ?, ?, 'manual', NULL, ?)",
                [account_id, as_of.isoformat(), balance_pence, now],
            )

    def balance_history(self, account_id: str) -> list[tuple[date, int]]:
        """One balance per day, in date order, from the household's side (a card's balance is
        negative: money owed). When statements or entries overlap, the latest one wins."""
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT as_of, balance_pence FROM account_balance WHERE account_id = ?"
                " ORDER BY as_of, id",
                [account_id],
            ).fetchall()
        by_day = {r["as_of"]: r["balance_pence"] for r in rows}
        return [(date.fromisoformat(day), pence) for day, pence in by_day.items()]

    def delete(self, statement_id: str) -> StatementRecord:
        """Remove a statement with its transactions and balances (the table's cascades)."""
        record = self.get(statement_id)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM statement WHERE id = ?", [statement_id])
        return record
