"""A migrated database with the knowledge stores, plus quick ways to add data."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.knowledge.categories import CategoryStore
from tuppence.knowledge.understanding import UnderstandingStore
from tuppence.knowledge.versions import KnowledgeVersions


@dataclass
class KnowledgeEnv:
    db: Database
    versions: KnowledgeVersions
    categories: CategoryStore
    understanding: UnderstandingStore

    @classmethod
    def create(cls, tmp_path: Path) -> KnowledgeEnv:
        db = Database(tmp_path / "t.db")
        migrate(db, tmp_path / "b")
        versions = KnowledgeVersions(db)
        env = cls(db, versions, CategoryStore(db, versions), UnderstandingStore(db, versions))
        env.categories.seed()
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO person (id, display_name, role, created_at, updated_at)"
                " VALUES ('p_alex', 'Alex Example', 'adult', 'x', 'x')"
            )
        env.add_account("a_current", "current", owners=["p_alex"])
        return env

    def add_account(
        self,
        account_id: str,
        kind: str,
        *,
        owners: list[str],
        nickname: str | None = None,
        last4: str | None = None,
        provider: str = "monzo",
    ) -> str:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO account (id, provider, provider_name, kind, nickname, last4,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'x', 'x')",
                [account_id, provider, provider.title(), kind, nickname or account_id, last4],
            )
            for person_id in owners:
                conn.execute(
                    "INSERT INTO account_owner (account_id, person_id) VALUES (?, ?)",
                    [account_id, person_id],
                )
        return account_id

    def add_statement(
        self, account_id: str, start: date | None = None, end: date | None = None
    ) -> str:
        statement_id = "s_" + secrets.token_hex(4)
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO statement (id, account_id, file_sha256, file_ext, original_filename,"
                " format, status, period_start, period_end, created_at, updated_at)"
                " VALUES (?, ?, ?, 'csv', 'x.csv', 'csv', 'imported', ?, ?, 'x', 'x')",
                [
                    statement_id,
                    account_id,
                    secrets.token_hex(32),
                    start.isoformat() if start else None,
                    end.isoformat() if end else None,
                ],
            )
        return statement_id

    def add_txn(
        self,
        day: date,
        pence: int,
        text: str,
        *,
        account_id: str = "a_current",
        statement_id: str | None = None,
        merchant_text: str | None = None,
        bank_type: str | None = None,
    ) -> str:
        statement_id = statement_id or self.add_statement(account_id)
        txn_id = "t_" + secrets.token_hex(6)
        with self.db.transaction() as conn:
            conn.execute(
                'INSERT INTO "transaction" (id, account_id, statement_id, date, amount_pence,'
                " raw_description, merchant_text, bank_type, source_ref, fingerprint, occurrence,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'L1', ?, 0, '2026-11-01T00:00:00Z')",
                [
                    txn_id,
                    account_id,
                    statement_id,
                    day.isoformat(),
                    pence,
                    text,
                    merchant_text,
                    bank_type,
                    secrets.token_hex(12),
                ],
            )
            # M3's link table: every statement that covers a row is linked to it.
            conn.execute(
                "INSERT INTO statement_transaction (statement_id, transaction_id, source_ref,"
                " match) VALUES (?, ?, 'L1', 'new')",
                [statement_id, txn_id],
            )
        return txn_id
