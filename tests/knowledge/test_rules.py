from datetime import date

import pytest
from pydantic import ValidationError

from tuppence.knowledge.authority import USER_RULE
from tuppence.knowledge.merchants import MerchantStore, infer_memory
from tuppence.knowledge.models import Decision
from tuppence.knowledge.rules import (
    RuleIn,
    RuleStore,
    TxnFacts,
    best_rule,
    load_txn_facts,
    matches,
    phrase_in,
)


def facts(text="CITY WATER DD", pence=-3115, **kw) -> TxnFacts:
    base = dict(
        id="t1",
        account_id="a_current",
        account_kind="current",
        date=date(2026, 10, 4),
        amount_pence=pence,
        text=text,
        merchant_id="m_water",
        owner_ids=frozenset({"p_alex"}),
    )
    base.update(kw)
    return TxnFacts(**base)


@pytest.fixture
def rules(kenv):
    store = RuleStore(kenv.db, kenv.versions, kenv.understanding)
    store.seed()
    return store


def test_phrases_match_whole_words_only():
    assert phrase_in("ATM", "ATM WITHDRAWAL HIGH ST")
    assert not phrase_in("ATM", "DENTAL TREATMENT")
    assert phrase_in("council tax", "LEEDS CITY COUNCIL - COUNCIL TAX 0012")
    assert not phrase_in("council tax", "TAX COUNCIL")
    assert not phrase_in("--", "ANYTHING")


def test_every_condition_must_hold():
    rule = RuleIn(
        merchant_id="m_water",
        direction="out",
        max_amount_pence=5000,
        set_category_id="housing.water",
    )
    assert matches(rule, facts())
    assert not matches(rule, facts(pence=3115))  # money in
    assert not matches(rule, facts(pence=-9000))  # too big
    assert not matches(rule, facts(merchant_id="m_other"))
    person = RuleIn(person_id="p_sam", set_category_id="other")
    assert not matches(person, facts()) and matches(person, facts(owner_ids=frozenset({"p_sam"})))


def test_the_most_specific_rule_wins(rules, kenv):
    broad, _ = rules.create(RuleIn(text_pattern="WATER", set_category_id="housing.water"))
    narrow, _ = rules.create(
        RuleIn(text_pattern="WATER", account_id="a_current", set_category_id="housing.repairs")
    )
    assert best_rule([broad, narrow], facts()).id == narrow.id
    assert best_rule([broad], facts(text="SPARKLING WATER CO")).id == broad.id
    assert best_rule([], facts()) is None


def test_bad_rules_are_refused():
    with pytest.raises(ValidationError, match="needs a merchant"):
        RuleIn(direction="out", set_category_id="other")
    with pytest.raises(ValidationError, match="needs to set"):
        RuleIn(text_pattern="ACME")
    with pytest.raises(ValidationError, match="smallest"):
        RuleIn(
            text_pattern="ACME", min_amount_pence=500, max_amount_pence=100, set_category_id="other"
        )


def test_seed_rules_cover_uk_basics(rules, kenv):
    assert rules.seed() == 0
    seeded = {r.id: r for r in rules.list()}
    assert seeded["seed-council-tax"].set_category_id == "housing.council-tax"
    assert seeded["seed-council-tax"].description == (
        "Payments mentioning “COUNCIL TAX” → Council tax"
    )
    t = kenv.add_txn(date(2026, 10, 1), -14200, "LEEDS CITY COUNCIL - COUNCIL TAX 0012")
    with kenv.db.connection() as conn:
        f = load_txn_facts(conn, [t])[0]
    assert best_rule(rules.list(), f).id == "seed-council-tax"


def test_preview_then_apply_to_past_rows_but_never_the_persons(rules, kenv):
    merchants = MerchantStore(kenv.db)
    ids = [kenv.add_txn(date(2026, m, 14), -999, "PAYPAL *STREAMLY") for m in (8, 9, 10)]
    with kenv.db.transaction() as conn:
        streamly = merchants.resolve(conn, "PAYPAL *STREAMLY", None)
        assert streamly is not None
        for t in ids:
            conn.execute(
                "UPDATE understanding SET merchant_id = ? WHERE transaction_id = ?",
                [streamly.id, t],
            )
        kenv.understanding.apply(
            conn,
            ids[0],
            Decision(
                decided_by="llm",
                authority=20,
                status="inferred",
                confidence=0.9,
                category_id="subscriptions.tv-streaming",
            ),
            actor="c",
            knowledge_version=0,
        )
    kenv.understanding.set_by_person(
        ids[1],
        expected_version=kenv.understanding.get(ids[1]).version,
        category_id="entertainment.games",
    )
    rule_in = RuleIn(merchant_id=streamly.id, set_category_id="subscriptions.tv-streaming")
    preview = rules.preview(rule_in)
    assert (preview.matches, preview.will_change, preview.kept_yours) == (3, 1, 1)
    rule, changed = rules.create(rule_in, created_from_transaction_id=ids[2])
    assert changed == 2  # the model's row is now the rule's; the unknown row too
    assert rule.description == "Payments to Streamly → Subscriptions › TV & video streaming"
    rows = {t: kenv.understanding.get(t) for t in ids}
    assert rows[ids[0]].decided_by == "rule" and rows[ids[0]].authority == USER_RULE
    assert rows[ids[1]].category_id == "entertainment.games"  # the person's choice stays
    assert rows[ids[2]].category_id == "subscriptions.tv-streaming"
    assert rules.get(rule.id).hit_count == 2
    _, released = rules.disable(rule.id, rule.version)
    assert released == 2 and kenv.understanding.get(ids[2]).status == "unknown"
    assert kenv.understanding.get(ids[2]).waiting == "queued"


def _restored_confirmed_row(kenv, text="PAYPAL *STREAMLY", *, transfer=False) -> str:
    """A confirmed row handed back after a statement re-read, whose category and merchant
    no longer exist (so both come back NULL)."""
    t = kenv.add_txn(date(2026, 9, 14), -999, text)
    with kenv.db.connection() as conn:
        fingerprint = conn.execute(
            'SELECT fingerprint FROM "transaction" WHERE id = ?', [t]
        ).fetchone()[0]
    kenv.understanding.set_by_person(
        t, expected_version=kenv.understanding.get(t).version, category_id="entertainment.games"
    )
    with kenv.db.transaction() as conn:
        conn.execute('DELETE FROM "transaction" WHERE id = ?', [t])
        conn.execute(
            "UPDATE understanding_carry SET category_id = 'gone', merchant_id = 'm_gone',"
            " is_transfer = ?",
            [int(transfer)],
        )
    statement = kenv.add_statement("a_current")
    new = "t_restored"
    with kenv.db.transaction() as conn:
        conn.execute(
            'INSERT INTO "transaction" (id, account_id, statement_id, date, amount_pence,'
            " raw_description, source_ref, fingerprint, occurrence, created_at)"
            " VALUES (?, 'a_current', ?, '2026-09-14', -999, ?, 'L1', ?, 0, '2026-11-01')",
            [new, statement, text, fingerprint],
        )
    return new


def test_restored_confirmed_rows_are_previewed_kept_and_never_overwritten(rules, kenv):
    new = _restored_confirmed_row(kenv, transfer=True)
    row = kenv.understanding.get(new)
    assert row.status == "confirmed" and row.category_id is None and row.merchant_id is None
    assert row.is_transfer and row.transfer_pair_id is None
    rule_in = RuleIn(text_pattern="STREAMLY", set_category_id="subscriptions.tv-streaming")
    preview = rules.preview(rule_in)
    assert (preview.matches, preview.will_change, preview.kept_yours) == (1, 0, 1)
    assert preview.examples[0]["category_id"] == ""
    rule, changed = rules.create(rule_in)
    assert changed == 0
    after = kenv.understanding.get(new)
    assert (after.category_id, after.decided_by, after.version) == (None, "human", row.version)
    _, released = rules.disable(rule.id, rule.version)
    assert released == 0 and kenv.understanding.get(new).status == "confirmed"


def test_restored_confirmed_memory_with_a_missing_merchant_does_not_crash(kenv):
    new = _restored_confirmed_row(kenv)
    merchants = MerchantStore(kenv.db)
    with kenv.db.transaction() as conn:
        m = merchants.resolve(conn, "PAYPAL *STREAMLY", None)
        assert m is not None
        merchants.confirm_memory(conn, m.id, category_id="subscriptions.tv-streaming")
        guess = infer_memory([("entertainment.games", None, 0.9, 5)] * 3)
        assert guess is not None
        assert merchants.remember(conn, m.id, guess, seen_count=3) is False  # confirmed: untouched
        assert MerchantStore.many_in(conn, [m.id, "m_gone"]).keys() == {m.id}
    assert merchants.get(m.id).default_category_id == "subscriptions.tv-streaming"
    assert kenv.understanding.get(new).status == "confirmed"
