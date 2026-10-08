import sqlite3
from datetime import date

import pytest

from tuppence.ingest.store import StatementStore

TABLES = {
    "knowledge_version",
    "category",
    "merchant",
    "merchant_variant",
    "rule",
    "understanding",
    "understanding_history",
    "category_refile",
    "commitment",
    "commitment_payment",
    "analysis_run",
}


def test_tables_exist(kenv):
    with kenv.db.connection() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names >= TABLES


def test_each_transaction_gets_one_understanding_row_and_loses_it_with_its_statement(kenv):
    statement = kenv.add_statement("a_current")
    t = kenv.add_txn(date(2026, 10, 1), -4218, "GREENBASKET STORES", statement_id=statement)
    with kenv.db.connection() as conn:
        assert (
            conn.execute(
                "SELECT status FROM understanding WHERE transaction_id = ?", [t]
            ).fetchone()[0]
            == "unknown"
        )
    StatementStore(kenv.db).delete(statement)  # M3's removal: rows no other statement covers
    with kenv.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM understanding").fetchone()[0] == 0


def test_the_shape_rules_hold(kenv):
    t = kenv.add_txn(date(2026, 10, 1), -4218, "GREENBASKET STORES")
    with pytest.raises(sqlite3.IntegrityError), kenv.db.transaction() as conn:
        conn.execute(
            "UPDATE understanding SET status = 'confirmed', decided_by = 'llm'"
            " WHERE transaction_id = ?",
            [t],
        )  # only the person confirms
    with pytest.raises(sqlite3.IntegrityError), kenv.db.transaction() as conn:
        conn.execute(
            "UPDATE understanding SET status = 'inferred' WHERE transaction_id = ?", [t]
        )  # a decision needs a decider
    with pytest.raises(sqlite3.IntegrityError), kenv.db.transaction() as conn:
        conn.execute(
            "INSERT INTO category (id, parent_id, level, label, kind, source, created_at,"
            " updated_at) VALUES ('toys', NULL, 1, 'Toys', 'spend', 'agent', 'x', 'x')"
        )
    with pytest.raises(sqlite3.IntegrityError), kenv.db.transaction() as conn:
        conn.execute(
            "INSERT INTO rule (id, description, direction, set_category_id, source,"
            " created_at, updated_at) VALUES ('r', 'x', 'out', 'other', 'user', 'x', 'x')"
        )


def test_knowledge_version_counts_up(kenv):
    assert kenv.versions.current() == 0
    with kenv.db.transaction() as conn:
        assert kenv.versions.bump(conn, "merchant", merchant_id="m_1", note="x") == 1
        assert kenv.versions.bump(conn, "category", category_id="food") == 2
    assert kenv.versions.current() == 2
