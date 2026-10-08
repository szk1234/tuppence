"""Sub-categories the Categoriser added are logged and can be undone (spec §8.2)."""

import pytest

from agents.helpers import crowd
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound
from tuppence.ingest.store import StatementStore

SPLIT = {
    "subcategories": [
        {"label": "Supermarkets", "merchants": ["M1", "M2"]},
        {"label": "Specialist shops", "merchants": ["M3", "M4"]},
    ]
}


def _split(aenv) -> list[str]:
    ids = crowd(aenv)
    aenv.llm.script.append(SPLIT)
    aenv.categorise(ids)
    return ids


def test_the_refile_is_logged_with_its_run_and_moves(aenv):
    ids = _split(aenv)
    (refile,) = aenv.refiles.list()
    assert refile.run_id == "run_test" and refile.parent_id == "food.groceries"
    assert sorted(refile.created_ids) == [
        "food.groceries.specialist-shops",
        "food.groceries.supermarkets",
    ]
    assert sorted(m["transaction_id"] for m in refile.moves) == sorted(ids)
    row = aenv.understanding.get(ids[0])
    assert row.evidence["refiled_from"] == "food.groceries" and row.decided_by == "llm"
    with aenv.db.connection() as conn:  # memory learnt from the moved rows
        homes = {r[0] for r in conn.execute("SELECT default_category_id FROM merchant")}
    assert homes == {"food.groceries.supermarkets", "food.groceries.specialist-shops"}


def test_undo_leaves_the_persons_rows_and_keeps_their_sub_category(aenv):
    ids = _split(aenv)
    mine = next(
        i for i in ids if aenv.understanding.get(i).category_id == "food.groceries.supermarkets"
    )
    row = aenv.understanding.get(mine)
    aenv.understanding.set_by_person(
        mine, expected_version=row.version, category_id="food.groceries.supermarkets"
    )
    (refile,) = aenv.refiles.list()
    assert aenv.refiles.undo(refile.id) == 11
    assert aenv.understanding.get(mine).category_id == "food.groceries.supermarkets"
    tree = aenv.categories.tree()
    assert tree.usable("food.groceries.supermarkets")  # it still holds the person's row
    assert not tree.usable("food.groceries.specialist-shops")
    with aenv.db.connection() as conn:
        homes = {r[0] for r in conn.execute("SELECT default_category_id FROM merchant")}
    assert homes == {"food.groceries"}
    assert aenv.refiles.list() == [] and len(aenv.refiles.list(include_undone=True)) == 1


def test_undo_once_only_and_only_what_exists(aenv):
    _split(aenv)
    (refile,) = aenv.refiles.list()
    aenv.refiles.undo(refile.id)
    with pytest.raises(InputError, match="already been undone"):
        aenv.refiles.undo(refile.id)
    with pytest.raises(NotFound):
        aenv.refiles.undo("rf_missing")


def test_a_split_that_came_to_nothing_is_remembered_but_not_listed(aenv):
    ids = crowd(aenv)  # the oracle never splits
    aenv.categorise(ids)
    assert aenv.refiles.list(include_undone=True) == []
    with aenv.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM category_refile").fetchone()[0] == 1


def test_undo_skips_rows_removed_since(aenv):
    """A moved row's statement was removed (or read again) after the split: the undo puts back
    the rest instead of failing every time."""
    ids = _split(aenv)
    with aenv.db.connection() as conn:
        statement = conn.execute(
            "SELECT statement_id FROM statement_transaction WHERE transaction_id = ?", [ids[0]]
        ).fetchone()[0]
    StatementStore(aenv.db).delete(statement)
    (refile,) = aenv.refiles.list()
    assert aenv.refiles.undo(refile.id) == 11
    assert {aenv.understanding.get(i).category_id for i in ids[1:]} == {"food.groceries"}
    assert aenv.refiles.list() == []
