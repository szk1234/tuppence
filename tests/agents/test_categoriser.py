import json
import re
from datetime import date, timedelta

import pytest

from agents.helpers import file_as
from tuppence.agents.categoriser import CategoriseOut, RefileOut
from tuppence.core.secrets import SecretUnreadable
from tuppence.llm.budget import estimate_tokens
from tuppence.llm.context import STRUCTURED_INSTRUCTION, ContextBudget
from tuppence.llm.jsonextract import to_strict_schema
from tuppence.llm.types import (
    AllModelsBlocked,
    AllModelsFailed,
    BudgetExceeded,
    LLMBadResponse,
    Message,
    NoModelConfigured,
    NoticeRequired,
)

D = date(2026, 10, 1)
NOTICE = "Confirm what Example AI will see before Tuppence uses it (Settings › AI)."


def reply(*rows: tuple[str, str, float]) -> dict:
    """A categorise or review reply: (ref, category id, confidence) for each row."""
    return {
        "transactions": [
            {"ref": ref, "category_id": cat, "who": "household", "confidence": conf, "reason": "x"}
            for ref, cat, conf in rows
        ]
    }


def test_rules_and_memory_first_then_the_model(aenv):
    tax = aenv.add_txn(D, -14200, "NORTHFIELD COUNCIL COUNCIL TAX")
    shop = aenv.add_txn(D, -4218, "GREENBASKET STORES 0873")
    cafe = aenv.add_txn(D, -340, "LITTLE CAFE")
    counts = aenv.categorise([tax, shop, cafe])
    assert counts["rule"] == 1 and counts["llm"] == 2
    assert len(aenv.llm.calls) == 1  # one batch for both
    tax_row, shop_row = aenv.understanding.get(tax), aenv.understanding.get(shop)
    assert (tax_row.category_id, tax_row.decided_by) == ("housing.council-tax", "rule")
    assert (shop_row.category_id, shop_row.decided_by, shop_row.status) == (
        "food.groceries",
        "llm",
        "inferred",
    )
    assert tax_row.who == "p_alex"  # a single-holder account: the holder
    assert "NORTHFIELD" not in aenv.llm.calls[0]["user"]  # rule rows never reach the model


def test_every_count_is_reported_and_calls_carry_the_run_id(aenv):
    counts = aenv.categorise([aenv.add_txn(D, -4218, "GREENBASKET STORES 0873")])
    assert set(counts) >= {
        "scope",
        "rule",
        "memory",
        "llm",
        "review",
        "deferred",
        "awaiting_ai",
        "unanswered",
        "bad_replies",
        "refiled",
        "new_categories",
        "memory_updates",
        "stopped",
        "ai_problem",
    }
    assert (counts["stopped"], counts["ai_problem"]) == ("", "")
    assert aenv.llm.calls[0]["run_id"] == "run_test"  # usage rows group by analysis run
    assert aenv.last_context.budget("categoriser").calls == 1


def test_rows_decided_under_the_current_version_are_not_asked_again(aenv):
    ids = [aenv.add_txn(D, -4218, "GREENBASKET STORES 0873")]
    aenv.categorise(ids)
    aenv.categorise(ids)
    assert len(aenv.llm.calls) == 1


def test_clear_memory_is_applied_in_code(aenv):
    first = [aenv.add_txn(date(2026, m, 3), -4218, "GREENBASKET STORES") for m in (7, 8, 9)]
    counts = aenv.categorise(first)
    assert counts["memory_updates"] == 1
    later = aenv.add_txn(date(2026, 10, 3), -5120, "GREENBASKET STORES 0873 LONDON")
    counts = aenv.categorise([later])
    assert counts["memory"] == 1 and len(aenv.llm.calls) == 1
    assert aenv.understanding.get(later).decided_by == "memory"


def test_low_confidence_goes_to_review(aenv):
    t = aenv.add_txn(D, -2000, "PAT EXAMPLE")
    counts = aenv.categorise([t])
    assert [c["task"] for c in aenv.llm.calls] == ["categorise", "review"]
    assert counts["review"] == 1
    row = aenv.understanding.get(t)
    assert (row.decided_by, row.status, row.category_id) == ("review", "guessed", "other")


def test_a_hostile_model_cannot_touch_other_rows_or_invent_categories(aenv):
    mine = aenv.add_txn(D, -999, "SOMETHING")
    confirmed = aenv.add_txn(D, -500, "ELSEWHERE")
    aenv.understanding.set_by_person(confirmed, expected_version=1, category_id="gifts.presents")
    aenv.llm.script = [
        {
            "transactions": [
                {
                    "ref": "T9",
                    "category_id": "food.groceries",
                    "who": "x",
                    "confidence": 1,
                    "reason": "x",
                },
                {"ref": "T1", "category_id": "made.up", "who": "x", "confidence": 1, "reason": "x"},
            ]
        }
    ]
    counts = aenv.categorise([mine, confirmed])
    assert counts["llm"] == 0 and counts["unanswered"] == 1
    assert aenv.understanding.get(mine).status == "unknown"
    assert aenv.understanding.get(mine).waiting == "deferred"
    assert aenv.understanding.get(confirmed).category_id == "gifts.presents"


def _reread(env, txn_id: str) -> str:
    """The statement read again: the row is deleted and inserted with a new id and the same
    fingerprint, so the person's confirmed understanding is restored (R-M4-2)."""
    with env.db.transaction() as conn:
        row = conn.execute('SELECT * FROM "transaction" WHERE id = ?', [txn_id]).fetchone()
        conn.execute('DELETE FROM "transaction" WHERE id = ?', [txn_id])
        new_id = "t_again_" + txn_id[2:]
        conn.execute(
            'INSERT INTO "transaction" (id, account_id, statement_id, date, amount_pence,'
            " raw_description, source_ref, fingerprint, occurrence, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            [
                new_id,
                row["account_id"],
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


def test_confirmed_rows_are_never_sent_to_the_model_or_changed(aenv):
    """Review Focus 3, with the rows a re-read restores: confirmed, but with no category (it
    was retired meanwhile) and no merchant, or a transfer with no pair."""
    plain = aenv.add_txn(D, -500, "CONFIRMED PLAIN")
    aenv.understanding.set_by_person(plain, expected_version=1, category_id="gifts.presents")
    gone = aenv.add_txn(D, -600, "CONFIRMED CATEGORY GONE")
    aenv.understanding.set_by_person(gone, expected_version=1, category_id="pets.vet")
    moved = aenv.add_txn(D, -700, "CONFIRMED TRANSFER")
    aenv.understanding.set_by_person(moved, expected_version=1, is_transfer=True)
    aenv.categories.retire("pets", aenv.categories.get("pets").version)
    gone, moved = _reread(aenv, gone), _reread(aenv, moved)
    mine = aenv.add_txn(D, -999, "SOMETHING ELSE")
    before = {
        t: (aenv.understanding.get(t), aenv.understanding.history(t)) for t in (plain, gone, moved)
    }
    restored = before[gone][0]
    assert (restored.status, restored.category_id, restored.merchant_id) == (
        "confirmed",
        None,
        None,
    )
    assert before[moved][0].is_transfer and before[moved][0].transfer_pair_id is None
    aenv.llm.script = [reply(*((f"T{n}", "food.groceries", 1.0) for n in range(1, 5)))]
    counts = aenv.categorise([plain, gone, moved, mine])
    assert counts["scope"] == 4 and counts["llm"] == 1
    assert "CONFIRMED" not in "\n".join(aenv.prompts())
    for txn_id, (row, history) in before.items():
        assert aenv.understanding.get(txn_id) == row
        assert aenv.understanding.history(txn_id) == history
    assert aenv.understanding.get(mine).category_id == "food.groceries"


def test_garbage_twice_is_deferred_not_fatal(aenv):
    t = aenv.add_txn(D, -999, "SOMETHING")
    aenv.llm.script = ["not json at all"]
    counts = aenv.categorise([t])
    assert counts["bad_replies"] == 1 and aenv.understanding.get(t).waiting == "deferred"
    assert counts["stopped"] == ""  # a bad reply defers its batch; the run carries on


def test_a_bad_reply_defers_only_its_batch(aenv):
    aenv.categoriser_manifest.limits["max_rows_per_batch"] = 1
    first = aenv.add_txn(D, -999, "GREENBASKET STORES")
    second = aenv.add_txn(D + timedelta(days=1), -340, "LITTLE CAFE")
    aenv.llm.script = ["not json at all"]
    counts = aenv.categorise([first, second])
    assert (counts["bad_replies"], counts["llm"], counts["stopped"]) == (1, 1, "")
    assert aenv.understanding.get(first).waiting == "deferred"
    assert aenv.understanding.get(second).category_id == "food.eating-out"


def test_budget_stops_cleanly_and_defers_the_rest(aenv):
    aenv.categoriser_manifest.limits["max_rows_per_batch"] = 1
    ids = [aenv.add_txn(D, -100 * (i + 1), f"SHOP NUMBER {chr(65 + i)}") for i in range(4)]
    aenv.llm.script = [None, BudgetExceeded("This run reached its limit of 1 AI calls.")]
    aenv.llm.script[0] = {
        "transactions": [
            {
                "ref": "T1",
                "category_id": "other",
                "who": "household",
                "confidence": 0.9,
                "reason": "x",
            }
        ]
    }
    counts = aenv.categorise(ids)
    assert counts["llm"] == 1 and counts["deferred"] == 3 and counts["stopped"] == "budget"
    waiting = [aenv.understanding.get(i).waiting for i in ids]
    assert waiting.count("deferred") == 3


def test_the_specialists_own_call_limit_stops_it(aenv):
    """The real budgets, not a scripted error: one call allowed, so the second batch and
    everything after it waits for the next run."""
    aenv.categoriser_manifest.limits["max_rows_per_batch"] = 1
    ids = [aenv.add_txn(D + timedelta(days=i), -100, f"GREENBASKET {i}") for i in range(3)]
    counts = aenv.categorise(ids, calls=1)
    assert (counts["llm"], counts["deferred"], counts["stopped"]) == (1, 2, "budget")
    assert aenv.last_context.budget("categoriser").calls == 1


def test_no_model_means_awaiting_ai(aenv):
    t = aenv.add_txn(D, -999, "SOMETHING")

    def no_model(task):
        raise NoModelConfigured("Choose an AI model in Settings › AI.")

    aenv.window_for = no_model
    counts = aenv.categorise([t])
    assert counts["stopped"] == "awaiting_ai" and aenv.understanding.get(t).waiting == "awaiting_ai"
    assert counts["ai_problem"] == "Choose an AI model in Settings › AI."
    aenv.window_for = lambda task: 8192
    aenv.llm.script = [AllModelsFailed(["m: down"])]
    counts = aenv.categorise([t])
    assert counts["stopped"] == "awaiting_ai" and "m: down" in counts["ai_problem"]


@pytest.mark.parametrize(
    "problem, waiting, stopped",
    [
        (BudgetExceeded("This run reached its time limit of 600 seconds."), "deferred", "budget"),
        (LLMBadResponse("odd"), "deferred", ""),
        (NoModelConfigured("Choose an AI model in Settings › AI."), "awaiting_ai", "awaiting_ai"),
        (AllModelsBlocked("Local only is on, so Tuppence didn't contact x."), "awaiting_ai",
         "awaiting_ai"),
        (NoticeRequired(NOTICE), "awaiting_ai", "awaiting_ai"),
        (SecretUnreadable("The saved API key can't be read."), "awaiting_ai", "awaiting_ai"),
    ],
)  # fmt: skip
def test_working_out_the_window_never_fails_the_run(aenv, problem, waiting, stopped):
    """G5: every context_window() call catches budget, bad-reply and AI problems in order."""
    t = aenv.add_txn(D, -999, "SOMETHING")

    def window(task):
        raise problem

    aenv.window_for = window
    counts = aenv.categorise([t])
    assert aenv.understanding.get(t).waiting == waiting
    assert counts["stopped"] == stopped and counts[waiting] == 1
    assert counts["ai_problem"] == ("" if waiting == "deferred" else str(problem))
    assert aenv.llm.calls == []


@pytest.mark.parametrize(
    "problem, text",
    [
        (NoticeRequired(NOTICE), "Confirm what Example AI will see"),
        (AllModelsBlocked("Local only is on, so Tuppence didn't contact x."), "Local only is on"),
        (SecretUnreadable("The saved API key can't be read."), "can't be read"),
        (AllModelsFailed(["Example AI / m: HTTP 500"]), "No AI model could answer"),
    ],
)
def test_an_ai_problem_leaves_the_rest_awaiting_ai_with_the_reason(aenv, problem, text):
    aenv.categoriser_manifest.limits["max_rows_per_batch"] = 1
    ids = [aenv.add_txn(D + timedelta(days=i), -100, f"GREENBASKET {i}") for i in range(3)]
    aenv.llm.script = [reply(("T1", "food.groceries", 0.95)), problem]
    counts = aenv.categorise(ids)
    assert (counts["llm"], counts["awaiting_ai"], counts["stopped"]) == (1, 2, "awaiting_ai")
    assert text in counts["ai_problem"]
    assert [aenv.understanding.get(i).waiting for i in ids] == [None, "awaiting_ai", "awaiting_ai"]
    assert len(aenv.llm.calls) == 2  # the third row isn't tried once the AI is out of reach


def test_a_review_cut_short_by_the_budget_waits_and_is_done_next_run(aenv):
    t = aenv.add_txn(D, -2000, "PAT EXAMPLE")
    aenv.llm.script = [
        reply(("T1", "other", 0.4)),
        BudgetExceeded("This run reached its limit of 1 AI calls."),
    ]
    counts = aenv.categorise([t])
    assert (counts["stopped"], counts["deferred"], counts["review"]) == ("budget", 1, 0)
    row = aenv.understanding.get(t)
    assert (row.decided_by, row.status, row.waiting) == ("llm", "guessed", "deferred")
    counts = aenv.categorise([t])  # the review only: the first answer still stands
    assert [c["task"] for c in aenv.llm.calls] == ["categorise", "review", "review"]
    assert counts["review"] == 1 and counts["llm"] == 0
    row = aenv.understanding.get(t)
    assert (row.decided_by, row.waiting) == ("review", None)


def test_rows_waiting_for_review_when_the_budget_runs_out_are_deferred(aenv):
    aenv.categoriser_manifest.limits["max_rows_per_batch"] = 1
    unsure = aenv.add_txn(D, -2000, "PAT EXAMPLE")
    later = aenv.add_txn(D + timedelta(days=1), -1000, "SOMEONE ELSE")
    aenv.llm.script = [
        reply(("T1", "other", 0.4)),
        BudgetExceeded("This run reached its limit of 1 AI calls."),
    ]
    counts = aenv.categorise([unsure, later])
    assert (counts["stopped"], counts["deferred"]) == ("budget", 2)
    assert [c["task"] for c in aenv.llm.calls] == ["categorise", "categorise"]
    assert aenv.understanding.get(unsure).waiting == "deferred"
    assert aenv.understanding.get(later).waiting == "deferred"
    aenv.categorise([unsure, later])  # the unsure row joins the next run's review
    assert [c["task"] for c in aenv.llm.calls[2:]] == ["categorise", "review", "review"]
    assert {aenv.understanding.get(i).decided_by for i in (unsure, later)} == {"review"}


def test_a_review_that_cannot_reach_a_model_leaves_the_guess_awaiting_ai(aenv):
    t = aenv.add_txn(D, -2000, "PAT EXAMPLE")
    aenv.llm.script = [reply(("T1", "other", 0.4)), NoticeRequired(NOTICE)]
    counts = aenv.categorise([t])
    assert (counts["stopped"], counts["awaiting_ai"]) == ("awaiting_ai", 1)
    assert counts["ai_problem"] == NOTICE
    row = aenv.understanding.get(t)
    assert (row.decided_by, row.status, row.category_id, row.waiting) == (
        "llm",
        "guessed",
        "other",
        "awaiting_ai",
    )


def test_a_garbled_review_is_deferred_and_the_first_answer_stands(aenv):
    t = aenv.add_txn(D, -2000, "PAT EXAMPLE")
    aenv.llm.script = [reply(("T1", "other", 0.4)), "not json at all"]
    counts = aenv.categorise([t])
    assert (counts["bad_replies"], counts["stopped"], counts["review"]) == (1, "", 0)
    row = aenv.understanding.get(t)
    assert (row.decided_by, row.status, row.waiting) == ("llm", "guessed", "deferred")


def repair_turn_tokens(call: dict, schema: type) -> int:
    """The client's estimate for the worst repair turn of this call: its schema instruction,
    the messages, a bad reply as long as max_tokens allows (cut at 4,000 characters) and the
    longest "that wasn't valid" message."""
    strict = to_strict_schema(schema.model_json_schema())
    worst = [
        STRUCTURED_INSTRUCTION + json.dumps(strict),
        *call["messages"],
        "x" * min(4000, 4 * call["max_tokens"]),
        "That wasn't valid: " + "x" * 300 + ". Reply with only the corrected JSON.",
    ]
    return estimate_tokens([Message(role="user", content=c) for c in worst])


def test_a_small_model_gets_a_shallower_tree_and_smaller_batches(aenv):
    aenv.window = 2048
    ids = [aenv.add_txn(D, -100 - i, f"GREENBASKET STORES {i:04d}") for i in range(12)]
    aenv.categorise(ids)
    first = aenv.llm.calls[0]
    assert "transport.car.fuel" not in first["user"]  # level 3 left out
    assert len(aenv.llm.calls) >= 2 and first["max_tokens"] <= 512
    for call in aenv.llm.calls:  # each call's repair turn fits too (the client allows 60%)
        assert repair_turn_tokens(call, CategoriseOut) <= ContextBudget(2048).input_tokens


def test_a_model_too_small_for_even_three_rows_waits_for_a_bigger_one(aenv):
    aenv.window = 1024
    ids = [aenv.add_txn(D, -100 - i, f"GREENBASKET STORES {i:04d}") for i in range(3)]
    counts = aenv.categorise(ids)
    assert (counts["stopped"], counts["awaiting_ai"]) == ("awaiting_ai", 3)
    assert "context window (1,024 tokens) is too small" in counts["ai_problem"]
    assert aenv.llm.calls == []


def _crowd(aenv) -> list[str]:
    aenv.categoriser_manifest.limits["crowded_category_rows"] = 8
    names = ["GREENBASKET STORES", "VALUEMART", "FARMGATE BUTCHERS", "CRUSTY BAKERY"]
    sizes = [5000, 4000, 1500, 1000]  # so the merchants are listed M1..M4 in this order
    ids = [aenv.add_txn(date(2026, 9, d), -sizes[d % 4], names[d % 4]) for d in range(1, 13)]
    aenv.llm.script = [
        {
            "transactions": [
                {
                    "ref": f"T{n}",
                    "category_id": "food.groceries",
                    "who": "household",
                    "confidence": 0.9,
                    "reason": "food",
                }
                for n in range(1, 13)
            ]
        }
    ]
    return ids


def test_crowded_category_is_split_and_can_be_undone(aenv):
    ids = _crowd(aenv)
    aenv.llm.script.append(
        {
            "subcategories": [
                {"label": "Supermarkets", "merchants": ["M1", "M2"]},
                {"label": "Specialist shops", "merchants": ["M3", "M4"]},
                {"label": "Groceries", "merchants": ["M1"]},
            ]
        }
    )
    counts = aenv.categorise(ids)
    assert counts["new_categories"] == 2 and counts["refiled"] == 12
    tree = aenv.categories.tree()
    assert {c.id for c in tree.children("food.groceries")} == {
        "food.groceries.supermarkets",
        "food.groceries.specialist-shops",
    }
    assert all(aenv.understanding.get(i).category_id.startswith("food.groceries.") for i in ids)
    refile = aenv.refiles.list()[0]
    assert aenv.refiles.undo(refile.id) == 12
    assert {aenv.understanding.get(i).category_id for i in ids} == {"food.groceries"}
    assert not aenv.categories.tree().usable("food.groceries.supermarkets")
    aenv.categorise(ids)  # never asked to split the same category again
    assert len(aenv.llm.calls) == 2


def _refile_calls(aenv) -> list[dict]:
    return [c for c in aenv.llm.calls if c["user"].startswith("CATEGORY:")]


def test_a_refile_that_cannot_reach_a_model_is_asked_again_next_run(aenv):
    ids = _crowd(aenv)
    aenv.llm.script.append(AllModelsFailed(["Example AI / m: HTTP 500"]))
    counts = aenv.categorise(ids)
    assert (counts["llm"], counts["new_categories"], counts["stopped"]) == (12, 0, "awaiting_ai")
    assert "No AI model could answer" in counts["ai_problem"]
    assert aenv.refiles.list(include_undone=True) == []
    counts = aenv.categorise(ids)  # the oracle doesn't split, and the category is then settled
    assert len(_refile_calls(aenv)) == 2 and counts["stopped"] == ""
    aenv.categorise(ids)
    assert len(_refile_calls(aenv)) == 2


def test_a_garbled_refile_is_asked_again_next_run(aenv):
    ids = _crowd(aenv)
    aenv.llm.script.append("not json at all")
    counts = aenv.categorise(ids)
    assert (counts["bad_replies"], counts["new_categories"], counts["stopped"]) == (1, 0, "")
    aenv.categorise(ids)
    assert len(_refile_calls(aenv)) == 2


def test_a_refile_stopped_by_the_budget_is_asked_again_next_run(aenv):
    ids = _crowd(aenv)
    aenv.llm.script.append(BudgetExceeded("This run reached its limit of 1 AI calls."))
    counts = aenv.categorise(ids)
    assert (counts["stopped"], counts["new_categories"]) == ("budget", 0)
    aenv.categorise(ids)
    assert len(_refile_calls(aenv)) == 2


def test_a_small_model_is_shown_the_biggest_merchants_that_fit(aenv):
    """A refile prompt and its repair turn fit the window: the smaller merchants are left out,
    rather than the split skipped or the call refused by the client."""
    words = ["ALPHA", "BRAVO", "CHARLIE", "DELTA", "ECHO", "FOXTROT", "GOLF", "HOTEL", "INDIA",
             "JULIET", "KILO", "LIMA", "MIKE", "NOVEMBER", "OSCAR", "PAPA", "QUEBEC", "ROMEO",
             "SIERRA", "TANGO", "UNIFORM", "VICTOR", "WHISKEY", "XRAY"]  # fmt: skip
    ids = []
    for n, word in enumerate(words):
        for month in (8, 9):
            txn_id = aenv.add_txn(date(2026, month, 1), -(9000 - 100 * n), f"{word} FOOD HALL")
            file_as(aenv, txn_id, "food.groceries", aenv.merchants)
            ids.append(txn_id)
    aenv.categoriser_manifest.limits["crowded_category_rows"] = 8
    aenv.window = 2048
    counts = aenv.categorise(ids)
    assert counts["stopped"] == "" and len(aenv.llm.calls) == 1
    (call,) = _refile_calls(aenv)
    listed = [ln for ln in call["user"].splitlines() if re.match(r"M\d+: ", ln)]
    assert 4 <= len(listed) < len(words) and listed[0].startswith("M1: Alpha Food Hall")
    assert repair_turn_tokens(call, RefileOut) <= ContextBudget(2048).input_tokens


def test_a_rule_into_a_retired_category_files_nothing(aenv):
    t = aenv.add_txn(D, -14200, "NORTHFIELD COUNCIL COUNCIL TAX")
    tax = aenv.categories.get("housing.council-tax")
    aenv.categories.retire(tax.id, tax.version)
    counts = aenv.categorise([t])
    assert counts["rule"] == 0 and counts["llm"] == 1
    assert aenv.understanding.get(t).category_id != "housing.council-tax"


def test_rows_filed_into_a_category_since_retired_are_filed_again(aenv):
    """Retiring a category makes its rows stale: the next run files them again, whoever
    decided them (a rule, memory or the model), but never the person's."""
    shop = aenv.add_txn(D, -4218, "GREENBASKET STORES 0873")
    tax = aenv.add_txn(D, -14200, "NORTHFIELD COUNCIL COUNCIL TAX")
    aenv.categorise([shop, tax])
    assert aenv.understanding.get(tax).decided_by == "rule"
    for cid in ("food.groceries", "housing.council-tax"):
        aenv.categories.retire(cid, aenv.categories.get(cid).version)
    counts = aenv.categorise([shop, tax])
    assert counts["llm"] == 2
    for txn_id in (shop, tax):
        assert aenv.categories.tree().usable(aenv.understanding.get(txn_id).category_id)
