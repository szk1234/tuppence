"""G6: what the Categoriser puts in a prompt is scrubbed (spec §4.6). Account and card details
in a row's description, merchant or bank category, in MEMORY and in a refile's merchant list
never reach the model; words, dates and amounts do."""

import json
from datetime import date

from tuppence.ingest.sensitive import scrub

D = date(2026, 10, 1)
# The digits and details of every account, sort code, card, IBAN and postcode below.
SECRETS = [
    "20-00-00", "200000", "12345678", "40-11-62", "401162", "31926819", "4929123412341234",
    "4929", "4242", "GB29", "NWBK", "60161331926819", "6016", "EX1 2MP", "87654321", "55554444",
    "31415926", "0873",
]  # fmt: skip


def leaked(prompts: list[str]) -> list[str]:
    sent = "\n".join(prompts)
    squashed = sent.replace(" ", "")
    return [s for s in SECRETS if s in sent or s.replace(" ", "") in squashed]


def test_no_account_detail_in_a_row_reaches_a_prompt(aenv):
    """The description, merchant and bank category of each row, in the categorise prompt and
    the review prompt."""
    streamly = aenv.add_txn(D, -1099, "STREAMLY")
    with aenv.db.transaction() as conn:  # a merchant name that kept a number
        merchant = aenv.merchants.resolve(conn, "STREAMLY", None)
        conn.execute("UPDATE merchant SET name = 'Streamly 55554444' WHERE id = ?", [merchant.id])
    ids = [
        streamly,
        aenv.add_txn(D, -1200, "SORT 20-00-00 ACC 12345678"),
        aenv.add_txn(D, -1300, "TFR TO SAVINGS 40-11-62 31926819"),
        aenv.add_txn(D, -1400, "CARD 4929123412341234 GREENBASKET"),
        aenv.add_txn(D, -1500, "VISA X4242 LITTLE CAFE"),
        aenv.add_txn(D, -1600, "IBAN GB29NWBK60161331926819 PAYMENT"),
        aenv.add_txn(D, -1700, "FLORIST EXAMPLETOWN EX1 2MP", bank_category="Shops 87654321"),
        aenv.add_txn(D, -1800, "GREENBASKET STORES 0873", merchant_text="GREENBASKET 0873"),
    ]
    counts = aenv.categorise(ids)
    tasks = [c["task"] for c in aenv.llm.calls]
    assert "categorise" in tasks and "review" in tasks  # both prompts are checked
    assert counts["llm"] == len(ids)
    assert leaked(aenv.prompts()) == []
    sent = "\n".join(aenv.prompts())
    for kept in ("GREENBASKET", "LITTLE CAFE", "FLORIST", "SAVINGS", "2026-10-01", "-17.00"):
        assert kept in sent
    assert '"merchant": "Streamly <HIDDEN>"' in sent
    assert "<HIDDEN>" in sent and "Alex Example" in sent  # household names stay (§4.6)


def test_an_account_number_cut_by_the_length_limit_is_still_hidden(aenv):
    """A description is scrubbed whole and then shortened: cut first, the start of an account
    number at the limit would go out as a fragment that no longer looks like one."""
    text = "PAYMENT " + "A" * 109 + " 93716482 SHOP"
    at = text.index("93716482")
    assert at < 120 < at + 8
    assert any(ch.isdigit() for ch in scrub(text[:120]))  # the wrong order leaks "93"
    t = aenv.add_txn(D, -1200, text)
    aenv.categorise([t])
    rows = [
        json.loads(line)
        for call in aenv.llm.calls
        for line in call["user"].splitlines()
        if line.startswith('{"ref"')
    ]
    assert len(rows) == 2  # the categorise and the review prompt
    for row in rows:
        assert len(row["description"]) <= 120
        assert not any(ch.isdigit() for ch in row["description"]), row["description"]


def test_memory_and_merchant_names_are_scrubbed(aenv):
    first = aenv.add_txn(D, -2500, "VALUEMART")
    aenv.categorise([first])
    with aenv.db.transaction() as conn:  # a merchant name that kept a number, and its memory
        conn.execute(
            "UPDATE merchant SET name = 'Valuemart 55554444', memory = 'inferred',"
            " default_category_id = 'food.groceries', confidence = 0.5, seen_count = 1"
        )
    later = aenv.add_txn(date(2026, 10, 9), -2600, "VALUEMART")
    aenv.categorise([later])
    (call,) = aenv.llm.calls[1:]
    assert "MEMORY:\n- Valuemart <HIDDEN>: food.groceries (seen 1 times)" in call["user"]
    assert '"merchant": "Valuemart <HIDDEN>"' in call["user"]
    assert leaked(aenv.prompts()) == []


def test_refile_merchant_names_are_scrubbed(aenv):
    aenv.categoriser_manifest.limits["crowded_category_rows"] = 8
    aenv.categoriser_manifest.limits["max_refiles_per_run"] = 0
    names = ["GREENBASKET STORES", "VALUEMART", "FARMGATE BUTCHERS", "CRUSTY BAKERY"]
    ids = [aenv.add_txn(date(2026, 9, d), -1000 - d, names[d % 4]) for d in range(1, 13)]
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
    aenv.categorise(ids)
    with aenv.db.transaction() as conn:
        conn.execute("UPDATE merchant SET name = name || ' 31415926'")
    aenv.categoriser_manifest.limits["max_refiles_per_run"] = 1
    aenv.categorise(ids)
    (call,) = aenv.llm.calls[1:]
    assert call["user"].startswith("CATEGORY: food.groceries")
    assert call["user"].count("<HIDDEN>") == 4
    assert leaked(aenv.prompts()) == []
