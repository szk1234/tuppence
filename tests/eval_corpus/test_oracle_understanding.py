import json

from evals import oracle

USER = """TODAY: 2026-11-01
PEOPLE:
- p_alex: Alex Example (adult)
- household: everyone in the household
CATEGORIES:
food — Food & drink
  food.groceries — Groceries
other — Other spending
income — Income
MEMORY:
- Little Cafe: food.eating-out (seen 4 times)
TRANSACTIONS:
{"ref": "T1", "amount": "-42.18", "description": "GREENBASKET 0873", "merchant": "Greenbasket"}
{"ref": "T2", "amount": "-3.40", "description": "LITTLE CAFE", "merchant": "Little Cafe"}
{"ref": "T3", "amount": "20.00", "description": "PAT EXAMPLE", "merchant": "Pat Example"}
"""


def test_the_oracle_categorises_within_the_tree_it_was_given():
    reply = json.loads(
        oracle.reply(
            [
                {"role": "system", "content": f"... ({oracle.CATEGORISE_MARKER}) ..."},
                {"role": "user", "content": USER},
            ]
        )
    )
    got = {r["ref"]: (r["category_id"], r["confidence"]) for r in reply["transactions"]}
    assert got == {"T1": ("food.groceries", 0.95), "T2": ("food", 0.95), "T3": ("income", 0.4)}


def test_the_oracle_labels_and_never_splits():
    labels = json.loads(
        oracle.reply(
            [
                {"role": "system", "content": oracle.LABELS_MARKER},
                {
                    "role": "user",
                    "content": 'PAYMENTS:\n{"ref": "P1", "category_id": "subscriptions.music"}\n'
                    '{"ref": "P2", "category_id": "food.groceries"}',
                },
            ]
        )
    )
    assert labels == {
        "payments": [{"ref": "P1", "kind": "subscription"}, {"ref": "P2", "kind": "none"}]
    }
    refile = json.loads(
        oracle.reply(
            [
                {"role": "system", "content": oracle.REFILE_MARKER},
                {"role": "user", "content": "CATEGORY: food"},
            ]
        )
    )
    assert refile == {"subcategories": []}


def test_a_json_repair_turn_gets_a_real_answer():
    """The client's repair turn adds a reply and a nudge after the real request."""
    messages = [
        {"role": "system", "content": oracle.CATEGORISE_MARKER},
        {"role": "user", "content": USER},
        {"role": "assistant", "content": "not json"},
        {"role": "user", "content": "That was not valid JSON. Reply again with only the JSON."},
    ]
    reply = json.loads(oracle.reply(messages))
    assert [r["ref"] for r in reply["transactions"]] == ["T1", "T2", "T3"]
    payments = [
        {"role": "system", "content": oracle.LABELS_MARKER},
        {"role": "user", "content": 'PAYMENTS:\n{"ref": "P1", "category_id": "housing.energy"}'},
        {"role": "assistant", "content": "oops"},
        {"role": "user", "content": "Reply again with only the JSON."},
    ]
    assert json.loads(oracle.reply(payments)) == {"payments": [{"ref": "P1", "kind": "bill"}]}
