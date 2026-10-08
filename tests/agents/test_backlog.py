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


def test_sweep_queues_what_the_categoriser_will_act_on_first_then_older_doubts(aenv):
    d = date(2026, 10, 1)
    unknown = aenv.add_txn(d, -100, "A")
    guessed = aenv.add_txn(d, -90000, "B")
    low = aenv.add_txn(d, -5000, "C")
    sure = aenv.add_txn(d, -7000, "D")
    ruled = aenv.add_txn(d, -8000, "E")
    mine = aenv.add_txn(d, -9000, "F")
    decide(aenv, guessed, status="guessed", confidence=0.5)  # no second look yet
    decide(aenv, low, confidence=0.6)  # unsure, decided before the person's change below
    decide(aenv, sure, confidence=0.95)
    decide(aenv, ruled, by="rule", authority=USER_RULE, confidence=1.0)
    aenv.understanding.set_by_person(mine, expected_version=1, category_id="other")
    with aenv.db.transaction() as conn:
        counts = sweep(conn, revisit_below=0.7, cap=2)
    assert counts == {"unreviewed": 1, "unknown": 1}  # the small unknown row beats `low`
    with aenv.db.transaction() as conn:
        assert sweep(conn, revisit_below=0.7, cap=10) == {"low_confidence": 1}
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


def review(kenv, t):
    """A guess the review pass already decided, under the current knowledge version."""
    decide(kenv, t, by="review", status="guessed", confidence=0.5, version=kenv.versions.current())


def test_rows_already_reviewed_under_the_current_knowledge_are_left_out(aenv):
    d = date(2026, 10, 1)
    rows = [aenv.add_txn(d, -10000 - i, f"SHOP {i}") for i in range(3)]
    for t in rows:
        review(aenv, t)
    with aenv.db.transaction() as conn:
        assert sweep(conn, revisit_below=0.7, cap=10) == {}
    with aenv.db.transaction() as conn:  # something changed (a rule, say): one more look each
        aenv.versions.bump(conn, "rule", note="a new rule")
        assert sweep(conn, revisit_below=0.7, cap=10) == {"guessed": 3}


def test_the_cap_goes_to_stale_and_retired_rows_before_big_reviewed_guesses(aenv):
    """The final review's probe: with more reviewed guesses than the cap, a retired category's
    rows and a corrected merchant's rows were never queued, so never filed again."""
    d = date(2026, 10, 1)
    big = [aenv.add_txn(d, -90000 - i, f"BIG SHOP {i}") for i in range(4)]
    news = [aenv.add_txn(d, -250, "DAILY NEWS DIGITAL") for _ in range(2)]
    cafe = [aenv.add_txn(d, -300, "LITTLE CAFE") for _ in range(2)]
    for t in news:
        decide(aenv, t, category="subscriptions.news", confidence=0.9)
    with aenv.db.transaction() as conn:
        merchant = aenv.merchants.resolve(conn, "LITTLE CAFE", None)
        for t in cafe:
            conn.execute(
                "UPDATE understanding SET merchant_id = ? WHERE transaction_id = ?",
                [merchant.id, t],
            )
    for t in cafe:
        decide(aenv, t, by="review", status="guessed", category="food.eating-out", confidence=0.5)
    aenv.categories.retire("subscriptions.news", aenv.categories.get("subscriptions.news").version)
    first = aenv.understanding.get(cafe[0])
    aenv.understanding.set_by_person(cafe[0], expected_version=first.version, category_id="other")
    for t in big:
        review(aenv, t)
    with aenv.db.transaction() as conn:
        assert sweep(conn, revisit_below=0.7, cap=3) == {"retired": 2, "stale": 1}
        assert set(queued(conn, limit=10)) == {*news, cafe[1]}
    aenv.categorise(queued_ids(aenv))  # the next run: all three are filed again
    for t in news:
        assert aenv.understanding.get(t).category_id == "subscriptions"
    assert aenv.understanding.get(cafe[1]).decided_by == "llm"  # asked again
    assert all(aenv.understanding.get(t).waiting is None for t in (*news, cafe[1]))
    assert all(aenv.understanding.get(t).decided_by == "review" for t in big)  # untouched


def queued_ids(kenv):
    with kenv.db.connection() as conn:
        return queued(conn, limit=100)


def test_rows_the_categoriser_looked_at_and_kept_are_not_queued_again(aenv):
    """A stale row only code can decide (a rule's, a transfer pair's): the Categoriser looks,
    keeps it, and stamps it with the current knowledge, so the sweep doesn't queue it forever."""
    t = aenv.add_txn(date(2026, 10, 1), -999, "PAYPAL *STREAMLY")
    with aenv.db.transaction() as conn:
        merchant = aenv.merchants.resolve(conn, "PAYPAL *STREAMLY", None)
        conn.execute(
            "UPDATE understanding SET merchant_id = ? WHERE transaction_id = ?", [merchant.id, t]
        )
    decide(aenv, t, by="rule", authority=USER_RULE, confidence=1.0, category="subscriptions")
    with aenv.db.transaction() as conn:
        aenv.versions.bump(conn, "merchant", merchant_id=merchant.id, note="usually streaming")
        assert sweep(conn, revisit_below=0.7, cap=10) == {"stale": 1}
    aenv.categorise(queued_ids(aenv))
    row = aenv.understanding.get(t)
    assert (row.decided_by, row.category_id, row.waiting) == ("rule", "subscriptions", None)
    assert aenv.llm.calls == []
    with aenv.db.transaction() as conn:
        assert sweep(conn, revisit_below=0.7, cap=10) == {}


def test_a_guess_queued_for_its_second_look_gets_it(aenv):
    t = aenv.add_txn(date(2026, 10, 1), -2000, "PAT EXAMPLE")
    decide(aenv, t, status="guessed", confidence=0.4)
    with aenv.db.transaction() as conn:
        assert sweep(conn, revisit_below=0.7, cap=10) == {"unreviewed": 1}
    aenv.categorise(queued_ids(aenv))
    assert [c["task"] for c in aenv.llm.calls] == ["review"]
    row = aenv.understanding.get(t)
    assert (row.decided_by, row.waiting) == ("review", None)
    with aenv.db.transaction() as conn:
        assert sweep(conn, revisit_below=0.7, cap=10) == {}


def test_a_runs_own_memory_update_leaves_the_rows_that_agree_with_it_settled(aenv):
    """The run that teaches Tuppence a merchant's usual category bumps the knowledge version for
    that merchant. The rows the memory was learned from agree with it: they aren't stale, so
    the sweep doesn't send them straight back to the model. A row that disagrees is."""
    d = date(2026, 10, 1)
    rows = [aenv.add_txn(d, -4218 - i, "GREENBASKET STORES") for i in range(4)]
    aenv.llm.script = [
        {
            "transactions": [
                {
                    "ref": f"T{n}",
                    "category_id": cat,
                    "who": "household",
                    "confidence": 0.95,
                    "reason": "x",
                }
                for n, cat in enumerate(
                    ["food.groceries", "food.groceries", "food.groceries", "other"], start=1
                )
            ]
        }
    ]
    counts = aenv.categorise(rows)
    assert counts["memory_updates"] == 1
    with aenv.db.transaction() as conn:
        assert sweep(conn, revisit_below=0.7, cap=10) == {"stale": 1}
        [stale] = queued(conn, limit=10)
    assert aenv.understanding.get(stale).category_id == "other"  # the one that disagrees
