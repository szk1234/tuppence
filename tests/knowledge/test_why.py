from datetime import date

from tuppence.knowledge.authority import MODEL
from tuppence.knowledge.models import Decision
from tuppence.knowledge.rules import RuleIn, RuleStore
from tuppence.knowledge.why import explain


def why_of(kenv, t):
    with kenv.db.connection() as conn:
        return explain(
            conn,
            kenv.categories.tree(),
            kenv.understanding.get(t),
            kenv.understanding.history(t),
            kenv.versions.current(),
        )


def test_the_model_then_a_rule_then_the_person(kenv):
    t = kenv.add_txn(date(2026, 10, 5), -340, "LITTLE CAFE")
    assert why_of(kenv, t).steps == ["Tuppence hasn't looked at this yet."]
    with kenv.db.transaction() as conn:
        kenv.understanding.apply(
            conn,
            t,
            Decision(
                decided_by="llm",
                authority=MODEL,
                status="inferred",
                confidence=0.92,
                category_id="food.eating-out",
                evidence={"reason": "cafe"},
            ),
            actor="categoriser",
            knowledge_version=0,
        )
    why = why_of(kenv, t)
    assert why.status_label == "Sorted" and why.category_path == ["Food & drink", "Eating out"]
    assert why.steps == ["The AI chose this (92% sure): cafe."]
    rules = RuleStore(kenv.db, kenv.versions, kenv.understanding)
    rules.create(RuleIn(text_pattern="LITTLE CAFE", set_category_id="food.takeaway"))
    why = why_of(kenv, t)
    assert why.decided_by_label == "A rule" and why.rule is not None
    assert why.steps[0].startswith("Matched your rule “Payments mentioning “LITTLE CAFE”")
    row = kenv.understanding.set_by_person(
        t, expected_version=why.version, category_id="food.eating-out"
    )
    why = why_of(kenv, t)
    assert why.status_label == "You set this" and why.steps[-1].startswith("You set this on ")
    assert [h["who"] for h in why.history] == ["You", "A rule", "The AI"]
    assert row.version == why.version


def test_the_memory_step_does_not_print_a_missing_usual_category(kenv):
    t = kenv.add_txn(date(2026, 10, 5), -340, "LITTLE CAFE")
    with kenv.db.transaction() as conn:
        conn.execute(
            "INSERT INTO merchant (id, key, name, seen_count, created_at, updated_at)"
            " VALUES ('m_cafe', 'little cafe', 'Little Cafe', 4, 'x', 'x')"
        )
        kenv.understanding.apply(
            conn,
            t,
            Decision(
                decided_by="memory",
                authority=MODEL,
                status="guessed",
                confidence=0.6,
                category_id="food.eating-out",
                merchant_id="m_cafe",
            ),
            actor="categoriser",
            knowledge_version=0,
        )
    steps = why_of(kenv, t).steps
    assert steps == ["Tuppence has seen Little Cafe 4 times."]
    assert not any("None" in s for s in steps)
