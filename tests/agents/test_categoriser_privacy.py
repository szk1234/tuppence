"""G6: what the Categoriser puts in a prompt is scrubbed (spec §4.6). Account and card details
in a row's description, merchant or bank category, in MEMORY and in a refile's merchant list
never reach the model; words, dates and amounts do."""

import json
import logging
from datetime import date

from tuppence.ingest import sensitive
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


def _sent_rows(aenv) -> list[dict]:
    """Every transaction line the scripted model was sent (categorise and review)."""
    return [
        json.loads(line)
        for call in aenv.llm.calls
        for line in call["user"].splitlines()
        if line.startswith('{"ref"')
    ]


def test_a_bank_category_cut_at_its_limit_is_still_hidden(aenv):
    """M7: the bank category is scrubbed whole, then cut at 40 characters."""
    bank_category = "SHOPPING " + "B" * 28 + " 93716482 X"
    at = bank_category.index("93716482")
    assert at < 40 < at + 8
    assert any(ch.isdigit() for ch in scrub(bank_category[:40]))  # the wrong order leaks
    t = aenv.add_txn(D, -1200, "SOMEWHERE", bank_category=bank_category)
    aenv.categorise([t])
    rows = _sent_rows(aenv)
    assert len(rows) == 2
    for row in rows:
        assert len(row["bank_category"]) <= 40
        assert not any(ch.isdigit() for ch in row["bank_category"]), row["bank_category"]


def test_a_reason_that_quotes_a_detail_is_scrubbed_before_the_review(aenv):
    """M7: the first model's reason goes into the review prompt (given_reason): scrubbed."""
    t = aenv.add_txn(D, -1200, "SOMEWHERE")
    aenv.llm.script = [
        {
            "transactions": [
                {
                    "ref": "T1",
                    "category_id": "other",
                    "who": "household",
                    "confidence": 0.4,
                    "reason": "paid to SORT 20-00-00 ACC 12345678",
                }
            ]
        }
    ]
    aenv.categorise([t])
    review = aenv.llm.calls[1]
    assert review["task"] == "review"
    (row,) = [json.loads(ln) for ln in review["user"].splitlines() if ln.startswith('{"ref"')]
    assert row["given_reason"].startswith("paid to SORT <HIDDEN>")
    assert leaked(aenv.prompts()) == []


def test_text_that_cannot_be_checked_is_hidden_and_the_run_carries_on(aenv, monkeypatch, caplog):
    """M2: if the masking code itself fails on a text, that text is sent as <HIDDEN>: never
    raw, and never failing the run. The warning names no text and the failures are counted."""

    def broken(text, **kw):
        raise RuntimeError(f"cannot read {text}")

    monkeypatch.setattr(sensitive, "prepare_outbound", broken)
    t = aenv.add_txn(D, -1200, "SORT 20-00-00 ACC 12345678", bank_category="Shops 87654321")
    with caplog.at_level(logging.WARNING):
        counts = aenv.categorise([t])
    assert (counts["stopped"], counts["llm"]) == ("", 1)
    assert counts["scrub_failures"] >= 2
    for row in _sent_rows(aenv):
        assert row["description"] == "<HIDDEN>" and row["bank_category"] == "<HIDDEN>"
    assert leaked(aenv.prompts()) == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and all("cannot read" not in w and "12345678" not in w for w in warnings)


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
