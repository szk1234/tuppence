from datetime import date

from tuppence.agents.backlog import queued, sweep
from tuppence.knowledge.authority import MODEL, USER_RULE
from tuppence.knowledge.models import Decision


def decide(
    kenv,
    t,
    *,
    by="llm",
    authority=MODEL,
    status="inferred",
    confidence=0.9,
    category="other",
    version=0,
):
    with kenv.db.transaction() as conn:
        kenv.understanding.apply(
            conn,
            t,
            Decision(
                decided_by=by,
                authority=authority,
                status=status,
                confidence=confidence,
                category_id=category,
            ),
            actor="test",
            knowledge_version=version,
        )


def test_sweep_queues_doubtful_rows_biggest_first_and_capped(aenv):
    d = date(2026, 10, 1)
    unknown = aenv.add_txn(d, -100, "A")
    guessed = aenv.add_txn(d, -90000, "B")
    low = aenv.add_txn(d, -5000, "C")
    sure = aenv.add_txn(d, -7000, "D")
    ruled = aenv.add_txn(d, -8000, "E")
    mine = aenv.add_txn(d, -9000, "F")
    decide(aenv, guessed, status="guessed", confidence=0.5)
    decide(aenv, low, confidence=0.6)
    decide(aenv, sure, confidence=0.95)
    decide(aenv, ruled, by="rule", authority=USER_RULE, confidence=1.0)
    aenv.understanding.set_by_person(mine, expected_version=1, category_id="other")
    with aenv.db.transaction() as conn:
        counts = sweep(conn, revisit_below=0.7, cap=2)
    assert counts == {"guessed": 1, "low_confidence": 1}
    with aenv.db.transaction() as conn:
        assert sweep(conn, revisit_below=0.7, cap=10) == {"unknown": 1}
        assert queued(conn, limit=10) == [guessed, low, unknown]


def test_stale_rows_are_queued_after_a_change_to_their_merchant(aenv):
    t = aenv.add_txn(date(2026, 10, 1), -999, "PAYPAL *STREAMLY")
    with aenv.db.transaction() as conn:
        merchant = aenv.merchants.resolve(conn, "PAYPAL *STREAMLY", None)
        conn.execute(
            "UPDATE understanding SET merchant_id = ? WHERE transaction_id = ?", [merchant.id, t]
        )
    decide(aenv, t, confidence=0.95, version=0)
    with aenv.db.transaction() as conn:
        assert sweep(conn, revisit_below=0.7, cap=10) == {}
        aenv.versions.bump(conn, "merchant", merchant_id=merchant.id, note="usually streaming")
        assert sweep(conn, revisit_below=0.7, cap=10) == {"stale": 1}
