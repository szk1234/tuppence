import sqlite3
from datetime import date

import pytest

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.core.records import NotFound, VersionConflict
from tuppence.ingest.models import ParsedRow, ParsedStatement
from tuppence.ingest.store import StatementStore


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    with db.connection() as c:
        for account_id, kind in (("a_1", "current"), ("c_1", "credit_card")):
            c.execute(
                "INSERT INTO account (id, provider, provider_name, kind, nickname, created_at,"
                " updated_at) VALUES (?, 'monzo', 'Monzo', ?, 'Main', 'x', 'x')",
                [account_id, kind],
            )
    return StatementStore(db)


def row(ref, day, pence, desc, balance=None):
    return ParsedRow(
        ref=ref,
        date=date(2026, 10, day),
        amount_pence=pence,
        amount_text="x",
        raw_description=desc,
        balance_after_pence=balance,
    )


def october(*rows, opening=None, closing=None, perspective="household"):
    return ParsedStatement(
        importer="csv:test",
        perspective=perspective,
        rows=list(rows),
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
        opening_balance_pence=opening,
        closing_balance_pence=closing,
    )


def new(store, sha):
    return store.create(sha256=sha * 64, ext="csv", filename=f"{sha}.csv", kind="csv")


def test_create_find_and_versioned_updates(store):
    record = new(store, "a")
    assert record.status == "received" and record.version == 1 and record.check_errors == []
    assert store.find_by_sha("a" * 64).id == record.id and store.find_by_sha("b" * 64) is None
    updated = store.update_versioned(record.id, 1, status="needs_review", check_errors=["L2: x"])
    assert updated.version == 2 and updated.check_errors == ["L2: x"]
    with pytest.raises(VersionConflict):
        store.update_versioned(record.id, 1, status="failed")
    with pytest.raises(ValueError):
        store.update(record.id, version=9)
    with pytest.raises(NotFound):
        store.get("s_missing")
    with pytest.raises(NotFound):
        store.update("s_missing", status="failed")
    assert [r.id for r in store.list()] == [record.id] and store.unfinished() == []


def test_unfinished_lists_statements_part_way_through(store):
    waiting, reading, asked = new(store, "a"), new(store, "b"), new(store, "c")
    store.update(reading.id, status="parsing")
    store.update(asked.id, status="needs_account")
    assert sorted(store.unfinished()) == sorted([waiting.id, reading.id])


def test_layout_memory_keeps_the_latest_answer_last(store):
    for account_id in ("a_1", "c_1", "a_1"):
        store.remember_layout("csv:abc", account_id)
    assert store.remembered_accounts("csv:abc") == ["c_1", "a_1"]
    assert store.remembered_accounts("csv:none") == []


def test_persist_writes_rows_balance_and_result_once(store):
    record = new(store, "a")
    parsed = october(
        row("L2", 1, -4218, "Greenbasket Stores", 95782),
        row("L3", 17, 165000, "Acme Payroll Ltd", 260782),
        opening=100000,
        closing=260782,
    )
    result = store.persist(record.id, "a_1", parsed, balance_verified=True, stats={"importer": "x"})
    assert (result.inserted, result.duplicates_exact, result.duplicates_similar) == (2, 0, 0)
    saved = store.get(record.id)
    assert saved.status == "imported" and saved.account_id == "a_1" and saved.balance_verified
    assert saved.analysis_state == "pending" and saved.stats["persist"]["inserted"] == 2
    assert saved.period_start == date(2026, 10, 1) and saved.closing_balance_pence == 260782
    assert [t.amount_pence for t in store.transactions(record.id)] == [-4218, 165000]
    assert store.balance_history("a_1") == [(date(2026, 10, 31), 260782)]
    again = store.persist(record.id, "a_1", parsed, balance_verified=True, stats={})
    assert again.inserted == 2 and len(store.transactions(record.id)) == 2


def test_persist_remembers_the_account_for_the_layout_in_the_same_write(store):
    record = new(store, "a")
    store.update(record.id, layout_fingerprint="csv:abc")
    store.persist(
        record.id, "a_1", october(row("L2", 1, -4218, "Shop")), balance_verified=False, stats={}
    )
    assert store.remembered_accounts("csv:abc") == ["a_1"]


def test_persist_on_behalf_of_the_person_checks_the_version(store):
    record = new(store, "a")
    store.update(record.id, status="needs_review", layout_fingerprint="csv:abc")
    with pytest.raises(VersionConflict):
        store.persist(
            record.id,
            "a_1",
            october(row("L2", 1, -4218, "Shop")),
            balance_verified=False,
            stats={},
            expected_version=1,
        )
    assert store.transactions(record.id) == [] and store.get(record.id).status == "needs_review"
    assert store.remembered_accounts("csv:abc") == []  # nothing of the refused write is kept


def test_a_failed_write_leaves_nothing_behind(store):
    record = new(store, "a")
    parsed = october(row("L2", 1, -4218, "Shop"), row("L3", 2, 0, "Not money"))  # 0 is refused
    with pytest.raises(sqlite3.IntegrityError):
        store.persist(record.id, "a_1", parsed, balance_verified=False, stats={})
    assert store.transactions(record.id) == [] and store.get(record.id).status == "received"


def test_card_balance_is_stored_as_money_owed(store):
    record = new(store, "c")
    parsed = october(
        row("L2", 2, -6420, "Greenbasket Stores"), opening=0, closing=6420, perspective="card"
    )
    store.persist(record.id, "c_1", parsed, balance_verified=True, stats={})
    assert store.balance_history("c_1") == [(date(2026, 10, 31), -6420)]


def test_rows_seen_in_another_statement_are_not_stored_twice(store):
    first, second = new(store, "a"), new(store, "b")
    store.persist(
        first.id,
        "a_1",
        october(row("L2", 1, -4218, "Greenbasket Stores"), row("L3", 5, -340, "Little Cafe")),
        balance_verified=False,
        stats={},
    )
    result = store.persist(
        second.id,
        "a_1",
        october(
            row("P1L4", 1, -4218, "Greenbasket Stores"),
            row("P1L5", 5, -340, "LITTLE CAFE LONDON"),
            row("P1L6", 9, -2890, "Northline Rail"),
        ),
        balance_verified=False,
        stats={},
    )
    assert (result.inserted, result.duplicates_exact, result.duplicates_similar) == (1, 1, 1)
    assert stored(store) == 3
    # the second statement lists every row it covers, as its own lines
    assert [(t.source_ref, t.raw_description, t.match) for t in store.transactions(second.id)] == [
        ("P1L4", "Greenbasket Stores", "exact"),
        ("P1L5", "Little Cafe", "similar"),
        ("P1L6", "Northline Rail", "new"),
    ]


def stored(store, account_id="a_1"):
    with store.db.connection() as conn:
        return conn.execute(
            'SELECT count(*) FROM "transaction" WHERE account_id = ?', [account_id]
        ).fetchone()[0]


def overlap(store):
    """Statement A covers rows 1-3; B covers 2-3 again (one reworded) and adds row 4."""
    a, b = new(store, "a"), new(store, "b")
    store.persist(
        a.id,
        "a_1",
        october(
            row("L2", 1, -4218, "Greenbasket Stores"),
            row("L3", 5, -340, "Little Cafe"),
            row("L4", 9, -2890, "Northline Rail"),
            opening=100000,
            closing=92552,
        ),
        balance_verified=True,
        stats={},
    )
    store.persist(
        b.id,
        "a_1",
        october(
            row("P1L1", 5, -340, "LITTLE CAFE LONDON"),
            row("P1L2", 9, -2890, "Northline Rail"),
            row("P1L3", 12, -3115, "City Water"),
            opening=95782,
            closing=89437,
        ),
        balance_verified=True,
        stats={},
    )
    return a, b


def descriptions(store, statement_id):
    return [t.raw_description for t in store.transactions(statement_id)]


def test_removing_a_statement_keeps_the_rows_another_statement_covers(store):
    a, b = overlap(store)
    assert stored(store) == 4
    store.delete(a.id)
    assert stored(store) == 3  # Greenbasket was only on A
    assert descriptions(store, b.id) == ["Little Cafe", "Northline Rail", "City Water"]
    with store.db.connection() as conn:
        owners = {r[0] for r in conn.execute('SELECT statement_id FROM "transaction"')}
    assert owners == {b.id}  # the rows A first stored now count as B's
    assert store.balance_history("a_1") == [(date(2026, 10, 31), 89437)]
    store.delete(b.id)
    assert stored(store) == 0 and store.balance_history("a_1") == []


def test_removing_the_later_statement_keeps_the_earlier_ones_rows(store):
    a, b = overlap(store)
    ids_before = {t.id for t in store.transactions(a.id)}
    store.delete(b.id)
    assert stored(store) == 3
    assert {t.id for t in store.transactions(a.id)} == ids_before  # ids never change
    assert store.balance_history("a_1") == [(date(2026, 10, 31), 92552)]


def test_a_balance_two_statements_report_survives_either_going(store):
    a, b = new(store, "a"), new(store, "b")
    parsed = october(row("L2", 1, -4218, "Shop"), opening=0, closing=-4218)
    store.persist(a.id, "a_1", parsed, balance_verified=True, stats={})
    store.persist(b.id, "a_1", parsed, balance_verified=True, stats={})
    store.delete(a.id)
    assert store.balance_history("a_1") == [(date(2026, 10, 31), -4218)]
    assert descriptions(store, b.id) == ["Shop"] and stored(store) == 1


def test_reopening_an_imported_statement_takes_its_import_back(store):
    a, b = overlap(store)
    reopened = store.reopen(b.id, store.get(b.id).version, status="received", run=2)
    assert reopened.status == "received" and reopened.analysis_state == "none"
    assert store.transactions(b.id) == [] and stored(store) == 3  # City Water was only on B
    assert descriptions(store, a.id) == ["Greenbasket Stores", "Little Cafe", "Northline Rail"]
    with pytest.raises(VersionConflict):
        store.reopen(a.id, 99, status="received")


def test_one_balance_per_day_however_many_statements_or_manual_entries(store):
    first, second = new(store, "a"), new(store, "b")
    parsed = october(row("L2", 1, -4218, "Shop"), opening=0, closing=-4218)
    store.persist(first.id, "a_1", parsed, balance_verified=True, stats={})
    store.persist(second.id, "a_1", parsed, balance_verified=True, stats={})  # the same month
    assert store.balance_history("a_1") == [(date(2026, 10, 31), -4218)]
    store.set_manual_balance("a_1", date(2026, 11, 5), 50000)
    store.set_manual_balance("a_1", date(2026, 11, 5), 51000)  # corrected the same day
    assert store.balance_history("a_1") == [
        (date(2026, 10, 31), -4218),
        (date(2026, 11, 5), 51000),
    ]
    with store.db.connection() as conn:
        manual = conn.execute(
            "SELECT count(*) FROM account_balance WHERE statement_id IS NULL"
        ).fetchone()[0]
    assert manual == 1


def test_delete_removes_rows_and_balances(store):
    record = new(store, "a")
    store.persist(
        record.id,
        "a_1",
        october(row("L2", 1, -4218, "Shop"), opening=0, closing=-4218),
        balance_verified=True,
        stats={},
    )
    store.delete(record.id)
    assert store.transactions(record.id) == [] and store.balance_history("a_1") == []
    with pytest.raises(NotFound):
        store.delete(record.id)
