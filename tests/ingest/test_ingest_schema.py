import sqlite3

import pytest

from tuppence.core.db import Database
from tuppence.core.migrate import migrate

INSERT_TXN = (
    'INSERT INTO "transaction" (id, account_id, statement_id, date, amount_pence, raw_description,'
    " source_ref, fingerprint, occurrence, created_at)"
    " VALUES (?, 'a_1', 's_1', ?, ?, 'Greenbasket Stores', 'L2', ?, 0, 'x')"
)
INSERT_STATEMENT = (
    "INSERT INTO statement (id, file_sha256, file_ext, original_filename, format, created_at,"
    " updated_at) VALUES (?, 'abc', 'csv', 'monzo.csv', 'csv', 'x', 'x')"
)


@pytest.fixture
def conn(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    with db.connection() as c:
        c.execute(
            "INSERT INTO account (id, provider, provider_name, kind, nickname, created_at,"
            " updated_at) VALUES ('a_1', 'monzo', 'Monzo', 'current', 'Main', 'x', 'x')"
        )
        c.execute(INSERT_STATEMENT, ["s_1"])
        yield c


def add_txn(c, tid, *, fp="fp1", amount=-4218, day="2026-10-01"):
    c.execute(INSERT_TXN, [tid, day, amount, fp])


def test_tables_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"statement", "transaction", "account_balance", "csv_layout", "layout_memory"} <= names


def test_fingerprint_is_unique_per_account(conn):
    add_txn(conn, "t_1")
    with pytest.raises(sqlite3.IntegrityError):
        add_txn(conn, "t_2")


def test_transactions_never_change(conn):
    add_txn(conn, "t_1")
    with pytest.raises(sqlite3.IntegrityError, match="never change"):
        conn.execute('UPDATE "transaction" SET amount_pence = 1 WHERE id = ?', ["t_1"])


def test_zero_amounts_and_bad_dates_are_refused(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add_txn(conn, "t_1", amount=0)
    with pytest.raises(sqlite3.IntegrityError):
        add_txn(conn, "t_2", fp="fp2", day="01/10/2026")


def test_imported_needs_an_account_and_removing_a_statement_removes_its_rows(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE statement SET status = 'imported' WHERE id = 's_1'")
    add_txn(conn, "t_1")
    conn.execute("DELETE FROM statement WHERE id = 's_1'")
    assert conn.execute('SELECT count(*) FROM "transaction"').fetchone()[0] == 0


def test_one_statement_per_file(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(INSERT_STATEMENT, ["s_2"])
