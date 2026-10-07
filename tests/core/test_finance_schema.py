import sqlite3

import pytest

from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.migrate import migrate
from tuppence.core.providers_uk import PROVIDERS, provider_name


@pytest.fixture
def conn(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    with db.connection() as c:
        yield c


def test_tables_exist(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "account",
        "account_owner",
        "income_source",
        "debt",
        "debt_entry",
        "goal",
        "onboarding_step",
    } <= names


def test_last4_must_be_four_digits(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO account (id, provider, provider_name, kind, nickname, last4,"
            " created_at, updated_at) VALUES ('a', 'monzo', 'Monzo', 'current', 'Main',"
            " '12a4', 'x', 'x')"
        )


def test_student_loan_plan_only_for_student_loans(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO debt (id, kind, lender, balance_pence, balance_date,"
            " student_loan_plan, created_at, updated_at)"
            " VALUES ('d', 'personal_loan', 'Acme', 100, '2026-10-01', 'plan2', 'x', 'x')"
        )


def test_providers():
    ids = [p.id for p in PROVIDERS]
    assert len(ids) == len(set(ids)) == 34 and "other" in ids
    assert provider_name("monzo") == "Monzo"
    with pytest.raises(InputError):
        provider_name("nope")
