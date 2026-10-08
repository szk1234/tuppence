import sqlite3
from datetime import date

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tuppence.core.errors import InputError
from tuppence.core.records import VersionConflict
from tuppence.knowledge.authority import (
    HUMAN,
    INFERRED_MEMORY,
    MODEL,
    USER_RULE,
    authority_for,
    may_replace,
)
from tuppence.knowledge.models import Decision, Understanding


def llm(category: str, confidence: float = 0.9) -> Decision:
    return Decision(
        decided_by="llm",
        authority=MODEL,
        status="inferred",
        confidence=confidence,
        category_id=category,
    )


def rule(category: str) -> Decision:
    return Decision(
        decided_by="rule",
        authority=USER_RULE,
        status="inferred",
        confidence=1.0,
        category_id=category,
        rule_id=None,
    )


def test_authority_order_matches_the_spec():
    assert authority_for("human") > authority_for("rule") > authority_for("rule", seed_rule=True)
    assert authority_for("rule", seed_rule=True) > authority_for("research")
    assert authority_for("research") == authority_for("memory", confirmed=True)
    assert authority_for("memory", confirmed=True) > authority_for("memory") > authority_for("llm")
    assert authority_for("llm") == authority_for("review") == MODEL
    row = Understanding(
        transaction_id="t", status="inferred", decided_by="rule", authority=USER_RULE
    )
    assert not may_replace(row, "llm", MODEL) and not may_replace(row, "memory", INFERRED_MEMORY)
    assert may_replace(row, "rule", USER_RULE) and may_replace(row, "human", HUMAN)
    assert not may_replace(
        row.model_copy(update={"status": "confirmed", "decided_by": "human", "authority": HUMAN}),
        "rule",
        USER_RULE,
    )


def test_every_row_has_an_understanding_from_the_start(kenv):
    t = kenv.add_txn(date(2026, 10, 1), -4218, "GREENBASKET STORES 0873")
    row = kenv.understanding.get(t)
    assert (row.status, row.decided_by, row.category_id, row.authority) == (
        "unknown",
        None,
        None,
        0,
    )


def test_decisions_write_history_and_respect_authority(kenv):
    t = kenv.add_txn(date(2026, 10, 1), -4218, "GREENBASKET STORES 0873")
    with kenv.db.transaction() as conn:
        assert kenv.understanding.apply(
            conn,
            t,
            llm("food.eating-out", 0.6),
            actor="categoriser",
            knowledge_version=0,
            reason="first look",
        )
        assert kenv.understanding.apply(
            conn, t, rule("food.groceries"), actor="rules", knowledge_version=0
        )
        assert not kenv.understanding.apply(
            conn, t, llm("other"), actor="categoriser", knowledge_version=0
        )  # the model can't beat a rule
    row = kenv.understanding.get(t)
    assert (row.category_id, row.decided_by, row.version) == ("food.groceries", "rule", 3)
    history = kenv.understanding.history(t)
    assert [(h.changed_by, h.category_id) for h in history] == [
        ("rules", "food.groceries"),
        ("categoriser", "food.eating-out"),
    ]


def test_the_same_answer_only_refreshes_the_knowledge_version(kenv):
    t = kenv.add_txn(date(2026, 10, 1), -340, "LITTLE CAFE")
    with kenv.db.transaction() as conn:
        kenv.understanding.apply(conn, t, llm("food.eating-out"), actor="c", knowledge_version=1)
        assert not kenv.understanding.apply(
            conn, t, llm("food.eating-out"), actor="c", knowledge_version=5
        )
    row = kenv.understanding.get(t)
    assert row.knowledge_version == 5 and len(kenv.understanding.history(t)) == 1


def test_the_person_confirms_and_agents_can_never_overwrite(kenv):
    t = kenv.add_txn(date(2026, 10, 5), -340, "LITTLE CAFE")
    row = kenv.understanding.set_by_person(t, expected_version=1, category_id="food.eating-out")
    assert (row.status, row.decided_by, row.authority) == ("confirmed", "human", HUMAN)
    assert kenv.versions.current() == 1  # a correction bumps the knowledge version
    with kenv.db.transaction() as conn:
        assert not kenv.understanding.apply(
            conn, t, rule("other"), actor="rules", knowledge_version=2
        )
        assert not kenv.understanding.release(conn, t, actor="transfer_matcher", reason="x")
    with pytest.raises(sqlite3.IntegrityError, match="only the person"), kenv.db.transaction() as c:
        c.execute(
            "UPDATE understanding SET decided_by = 'llm', status = 'inferred'"
            " WHERE transaction_id = ?",
            [t],
        )
    with pytest.raises(VersionConflict):
        kenv.understanding.set_by_person(t, expected_version=1, category_id="other")
    again = kenv.understanding.release_by_person(t, expected_version=row.version)
    assert again.status == "unknown" and again.waiting == "queued"


def test_marking_transfers(kenv):
    t = kenv.add_txn(date(2026, 10, 5), -20000, "TO SAVINGS")
    row = kenv.understanding.set_by_person(t, expected_version=1, is_transfer=True)
    assert row.is_transfer and row.category_id == "transfers.between-accounts"
    with pytest.raises(InputError, match="instead"):
        kenv.understanding.set_by_person(t, expected_version=row.version, is_transfer=False)
    row = kenv.understanding.set_by_person(
        t, expected_version=row.version, is_transfer=False, category_id="gifts.presents"
    )
    assert not row.is_transfer and row.category_id == "gifts.presents"


ACTIONS = st.lists(
    st.tuples(
        st.sampled_from(["llm", "review", "memory", "rule", "research", "person"]),
        st.sampled_from(["food.groceries", "food.eating-out", "other", "transfers.cash"]),
        st.floats(min_value=0, max_value=1),
    ),
    min_size=1,
    max_size=12,
)


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(actions=ACTIONS)
def test_a_confirmed_row_never_changes_without_the_person(kenv, actions):
    """Spec §10.2 property: once confirmed, only the person's own actions change a row."""
    t = kenv.add_txn(date(2026, 10, 1), -1000, "SOMEWHERE")
    confirmed: Understanding | None = None
    for actor, category, confidence in actions:
        if actor == "person":
            current = kenv.understanding.get(t)
            confirmed = kenv.understanding.set_by_person(
                t, expected_version=current.version, category_id=category
            )
            continue
        decision = Decision(
            decided_by=actor,
            authority=authority_for(actor),
            status="inferred",
            confidence=confidence,
            category_id=category,
        )
        with kenv.db.transaction() as conn:
            kenv.understanding.apply(conn, t, decision, actor=actor, knowledge_version=99)
            kenv.understanding.release(conn, t, actor=actor, reason="hostile")
        if confirmed is not None:
            now = kenv.understanding.get(t)
            assert (now.category_id, now.status, now.decided_by, now.version) == (
                confirmed.category_id,
                "confirmed",
                "human",
                confirmed.version,
            )


# --- corrections survive a re-read of a statement (R-M4-2) ---------------------------------


def _reread(kenv, txn_id: str, *, account_id: str = "a_current") -> str:
    """Delete the transaction (a statement removed) and insert the same one again, with a new
    id but the same fingerprint (the statement read again)."""
    with kenv.db.transaction() as conn:
        row = conn.execute('SELECT * FROM "transaction" WHERE id = ?', [txn_id]).fetchone()
        conn.execute('DELETE FROM "transaction" WHERE id = ?', [txn_id])
        new_id = "t_again_" + txn_id[2:]
        conn.execute(
            'INSERT INTO "transaction" (id, account_id, statement_id, date, amount_pence,'
            " raw_description, source_ref, fingerprint, occurrence, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            [
                new_id,
                account_id,
                row["statement_id"],
                row["date"],
                row["amount_pence"],
                row["raw_description"],
                row["source_ref"],
                row["fingerprint"],
                "2026-11-02T00:00:00Z",
            ],
        )
    return new_id


def _removed(kenv, txn_id: str) -> None:
    with kenv.db.transaction() as conn:
        conn.execute('DELETE FROM "transaction" WHERE id = ?', [txn_id])


def test_a_confirmed_correction_survives_a_reread(kenv):
    t = kenv.add_txn(date(2026, 10, 5), -340, "LITTLE CAFE")
    kenv.understanding.set_by_person(
        t, expected_version=1, category_id="food.eating-out", who="p_alex"
    )
    again = _reread(kenv, t)
    row = kenv.understanding.get(again)
    assert (row.status, row.decided_by, row.authority) == ("confirmed", "human", HUMAN)
    assert (row.category_id, row.who, row.version) == ("food.eating-out", "p_alex", 1)
    assert row.evidence["carried"] == "reread" and row.transfer_pair_id is None
    history = kenv.understanding.history(again)
    assert len(history) == 1 and history[0].status == "confirmed"
    assert history[0].category_id == "food.eating-out"
    with kenv.db.connection() as conn:  # used up
        assert conn.execute("SELECT COUNT(*) FROM understanding_carry").fetchone()[0] == 0
    # and it is still the person's: nothing else can change it
    with kenv.db.transaction() as conn:
        assert not kenv.understanding.apply(
            conn, again, rule("other"), actor="rules", knowledge_version=9
        )


def test_an_unconfirmed_understanding_is_not_carried(kenv):
    t = kenv.add_txn(date(2026, 10, 5), -340, "LITTLE CAFE")
    with kenv.db.transaction() as conn:
        kenv.understanding.apply(conn, t, llm("food.eating-out"), actor="c", knowledge_version=0)
    again = _reread(kenv, t)
    row = kenv.understanding.get(again)
    assert (row.status, row.category_id, row.decided_by) == ("unknown", None, None)
    with kenv.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM understanding_carry").fetchone()[0] == 0


def test_a_correction_is_not_restored_under_another_account(kenv):
    kenv.add_account("a_other", "current", owners=["p_alex"])
    t = kenv.add_txn(date(2026, 10, 5), -340, "LITTLE CAFE")
    kenv.understanding.set_by_person(t, expected_version=1, category_id="food.eating-out")
    elsewhere = _reread(kenv, t, account_id="a_other")
    assert kenv.understanding.get(elsewhere).status == "unknown"
    with kenv.db.connection() as conn:  # still waiting for its own account
        assert conn.execute("SELECT COUNT(*) FROM understanding_carry").fetchone()[0] == 1


def test_a_carried_category_that_no_longer_exists_restores_as_none(kenv):
    first = kenv.add_txn(date(2026, 10, 5), -340, "LITTLE CAFE")
    fingerprint = _fingerprint(kenv, first)
    kenv.understanding.set_by_person(
        first, expected_version=1, category_id="pets.vet", who="p_alex"
    )
    _removed(kenv, first)
    with kenv.db.transaction() as conn:  # the person's own category, removed outright
        conn.execute("UPDATE understanding_carry SET category_id = 'gone.away'")
    again = _insert_with(kenv, fingerprint)
    row = kenv.understanding.get(again)
    assert (row.status, row.decided_by, row.category_id, row.who) == (
        "confirmed",
        "human",
        None,
        "p_alex",
    )


def test_a_carried_category_that_was_retired_restores_as_none(kenv):
    first = kenv.add_txn(date(2026, 10, 5), -340, "LITTLE CAFE")
    fingerprint = _fingerprint(kenv, first)
    kenv.understanding.set_by_person(first, expected_version=1, category_id="pets.vet")
    _removed(kenv, first)
    kenv.categories.retire("pets", kenv.categories.get("pets").version)
    again = _insert_with(kenv, fingerprint)
    row = kenv.understanding.get(again)
    assert (row.status, row.decided_by, row.category_id) == ("confirmed", "human", None)


def _fingerprint(kenv, txn_id: str) -> str:
    with kenv.db.connection() as conn:
        return conn.execute(
            'SELECT fingerprint FROM "transaction" WHERE id = ?', [txn_id]
        ).fetchone()[0]


def _insert_with(kenv, fingerprint: str, *, account_id: str = "a_current") -> str:
    statement_id = kenv.add_statement(account_id)
    with kenv.db.transaction() as conn:
        conn.execute(
            'INSERT INTO "transaction" (id, account_id, statement_id, date, amount_pence,'
            " raw_description, source_ref, fingerprint, occurrence, created_at)"
            " VALUES ('t_back', ?, ?, '2026-10-05', -340, 'LITTLE CAFE', 'L1', ?, 0, 'x')",
            [account_id, statement_id, fingerprint],
        )
    return "t_back"


def test_removing_a_statement_keeps_the_corrections_on_the_rows_it_covered(kenv):
    from tuppence.ingest.store import StatementStore

    statement = kenv.add_statement("a_current")
    t = kenv.add_txn(date(2026, 10, 5), -340, "LITTLE CAFE", statement_id=statement)
    other = kenv.add_txn(date(2026, 10, 6), -500, "UNTOUCHED", statement_id=statement)
    kenv.understanding.set_by_person(t, expected_version=1, category_id="food.eating-out")
    StatementStore(kenv.db).delete(statement)
    with kenv.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM understanding").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM understanding_carry").fetchone()[0] == 1
    assert other


def _transfer(pair: str) -> Decision:
    return Decision(
        decided_by="llm",
        authority=MODEL,
        status="inferred",
        confidence=0.9,
        category_id="transfers.between-accounts",
        is_transfer=True,
        transfer_pair_id=pair,
    )


def _pair(kenv, a: str, b: str) -> None:
    with kenv.db.transaction() as conn:
        kenv.understanding.apply(conn, a, _transfer(b), actor="t", knowledge_version=0)
        kenv.understanding.apply(conn, b, _transfer(a), actor="t", knowledge_version=0)


def test_unmarking_a_transfer_leaves_a_partner_that_was_paired_elsewhere(kenv):
    a = kenv.add_txn(date(2026, 10, 5), -20000, "TO SAVINGS")
    b = kenv.add_txn(date(2026, 10, 5), 20000, "FROM CURRENT")
    c = kenv.add_txn(date(2026, 10, 6), -20000, "TO SAVINGS AGAIN")
    _pair(kenv, a, b)
    # b was since re-paired with c: a's pointer to b is stale.
    with kenv.db.transaction() as conn:
        kenv.understanding.apply(conn, b, _transfer(c), actor="t", knowledge_version=0)
        kenv.understanding.apply(conn, c, _transfer(b), actor="t", knowledge_version=0)
    row = kenv.understanding.set_by_person(
        a,
        expected_version=kenv.understanding.get(a).version,
        is_transfer=False,
        category_id="gifts.presents",
    )
    assert not row.is_transfer and row.transfer_pair_id is None
    for other, pair in ((b, c), (c, b)):
        kept = kenv.understanding.get(other)
        assert kept.is_transfer and kept.transfer_pair_id == pair and kept.status == "inferred"


def test_unmarking_a_transfer_still_releases_a_partner_that_points_back(kenv):
    a = kenv.add_txn(date(2026, 10, 5), -20000, "TO SAVINGS")
    b = kenv.add_txn(date(2026, 10, 5), 20000, "FROM CURRENT")
    _pair(kenv, a, b)
    kenv.understanding.set_by_person(
        a,
        expected_version=kenv.understanding.get(a).version,
        is_transfer=False,
        category_id="gifts.presents",
    )
    other = kenv.understanding.get(b)
    assert other.status == "unknown" and not other.is_transfer and other.transfer_pair_id is None


def test_letting_tuppence_decide_again_notes_a_confirmed_partner(kenv):
    a = kenv.add_txn(date(2026, 10, 5), -20000, "TO SAVINGS")
    b = kenv.add_txn(date(2026, 10, 5), 20000, "FROM CURRENT")
    _pair(kenv, a, b)
    kenv.understanding.set_by_person(
        b, expected_version=kenv.understanding.get(b).version, category_id="transfers.cash"
    )
    released = kenv.understanding.release_by_person(
        a, expected_version=kenv.understanding.get(a).version
    )
    assert released.evidence == {"released_by": "person", "partner_confirmed": b}
    assert kenv.understanding.get(b).status == "confirmed"
