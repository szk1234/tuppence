from datetime import date

from tuppence.knowledge.authority import MODEL
from tuppence.knowledge.models import Decision
from tuppence.knowledge.spending import SpendingFilter, breakdown, transactions

OCT = SpendingFilter(date(2026, 10, 1), date(2026, 10, 31))


def put(kenv, pence, text, category, day=5, **kw):
    t = kenv.add_txn(date(2026, 10, day), pence, text, **kw)
    if category:
        kind = kenv.categories.tree().kind_of(category)
        with kenv.db.transaction() as conn:
            kenv.understanding.apply(
                conn,
                t,
                Decision(
                    decided_by="llm",
                    authority=MODEL,
                    status="inferred",
                    confidence=0.9,
                    category_id=category,
                    who="p_alex",
                    is_transfer=kind == "transfer",
                ),
                actor="test",
                knowledge_version=0,
            )
    return t


def test_level_one_tiles_refunds_unsorted_income_and_transfers(kenv):
    put(kenv, -4218, "GREENBASKET", "food.groceries")
    put(kenv, -340, "LITTLE CAFE", "food.eating-out")
    put(kenv, 615, "HARBOUR PHARMACY REFUND", "health.pharmacy")
    put(kenv, -1000, "HARBOUR PHARMACY", "health.pharmacy")
    put(kenv, -2500, "MYSTERY", None)
    put(kenv, 165000, "ACME PAYROLL", "income.salary")
    put(kenv, -20000, "TO SAVINGS", "transfers.between-accounts")
    put(kenv, -5000, "ISA PLATFORM", "savings.investments")
    put(kenv, -999, "IGNORED", "food.groceries", day=1)
    with kenv.db.transaction() as conn:
        conn.execute(
            "UPDATE understanding SET ignored = 1 WHERE transaction_id IN"
            ' (SELECT id FROM "transaction" WHERE raw_description = ?)',
            ["IGNORED"],
        )
    with kenv.db.connection() as conn:
        top = breakdown(conn, kenv.categories.tree(), OCT)
    assert [(t.id, t.amount_pence) for t in top.tiles] == [
        ("food", 4558),
        ("unsorted", 2500),
        ("health", 385),
    ]
    assert top.total_pence == 7443 and top.money_in_pence == 165000 and top.saved_pence == 5000
    assert top.path[0].label == "All spending"


def test_drill_down_and_the_rows_behind_a_tile(kenv):
    put(kenv, -4218, "GREENBASKET", "food.groceries")
    put(kenv, -340, "LITTLE CAFE", "food.eating-out")
    put(kenv, -999, "FOOD SOMETHING", "food")
    with kenv.db.connection() as conn:
        tree = kenv.categories.tree()
        food = breakdown(conn, tree, OCT, "food")
        rows = transactions(conn, tree, OCT, category_id="food")
        unsorted = transactions(conn, tree, OCT, category_id="unsorted")
    assert [c.label for c in food.path] == ["All spending", "Food & drink"]
    assert [(t.id, t.amount_pence) for t in food.tiles] == [
        ("food.groceries", 4218),
        ("food.eating-out", 340),
    ]
    assert food.direct_pence == 999 and food.total_pence == 5557
    assert {r.description for r in rows} == {"GREENBASKET", "LITTLE CAFE", "FOOD SOMETHING"}
    assert rows[0].category_label is not None and unsorted == []


def test_filters(kenv):
    kenv.add_account("a_card", "credit_card", owners=["p_alex"])
    put(kenv, -4218, "GREENBASKET", "food.groceries")
    put(kenv, -340, "LITTLE CAFE", "food.eating-out", account_id="a_card")
    with kenv.db.connection() as conn:
        tree = kenv.categories.tree()
        card = breakdown(conn, tree, SpendingFilter(OCT.start, OCT.end, account_id="a_card"))
        guessed = breakdown(conn, tree, SpendingFilter(OCT.start, OCT.end, status="guessed"))
    assert card.total_pence == 340 and guessed.tiles == []


def test_unsorted_counts_only_money_out_and_opens_as_a_flat_list(kenv):
    put(kenv, -2500, "MYSTERY", None)
    put(kenv, 1000, "MYSTERY CREDIT", None)
    put(kenv, -340, "LITTLE CAFE", "food.eating-out")
    with kenv.db.connection() as conn:
        tree = kenv.categories.tree()
        top = breakdown(conn, tree, OCT)
        flat = breakdown(conn, tree, OCT, "unsorted")
        rows = transactions(conn, tree, OCT, category_id="unsorted")
    tile = next(t for t in top.tiles if t.id == "unsorted")
    assert (tile.amount_pence, tile.count) == (2500, 1)
    assert [c.label for c in flat.path] == ["All spending", "Not sorted yet"]
    assert (flat.total_pence, flat.direct_pence, flat.tiles) == (2500, 2500, [])
    assert [r.description for r in rows] == ["MYSTERY"]
