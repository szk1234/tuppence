import pytest

from tuppence.core.errors import InputError
from tuppence.knowledge.categories import SEED_CATEGORIES, CategoryTree, slugify


def test_seed_tree_is_well_formed():
    ids = [c[0] for c in SEED_CATEGORIES]
    assert len(ids) == len(set(ids))
    for cid, label, _, _ in SEED_CATEGORIES:
        assert slugify(cid.rsplit(".", 1)[-1]) == cid.rsplit(".", 1)[-1]
        assert 1 <= len(label) <= 40
        if "." in cid:
            assert cid.rsplit(".", 1)[0] in ids[: ids.index(cid)]  # parents come first
    roots = {c[0] for c in SEED_CATEGORIES if "." not in c[0]}
    assert {
        "housing",
        "transport",
        "food",
        "children",
        "subscriptions",
        "financial",
        "income",
        "savings",
        "transfers",
        "other",
    } <= roots


def test_seed_is_idempotent_and_keeps_retired(kenv):
    assert kenv.categories.seed() == 0
    kenv.categories.retire("pets", kenv.categories.get("pets").version)
    assert kenv.categories.seed() == 0
    tree = kenv.categories.tree()
    assert tree.get("pets").retired and tree.get("pets.vet").retired
    assert not tree.usable("pets.vet") and tree.usable("food.groceries")


def test_tree_paths_and_levels(kenv):
    tree = kenv.categories.tree()
    assert [c.id for c in tree.path("transport.car.fuel")] == [
        "transport",
        "transport.car",
        "transport.car.fuel",
    ]
    assert tree.ancestor_at("transport.car.fuel", 1) == "transport"
    assert tree.ancestor_at("transport.car.fuel", 2) == "transport.car"
    assert tree.ancestor_at("food", 2) is None
    assert "transport.car.fuel" in tree.descendants("transport")
    assert (
        tree.kind_of("transfers.cash") == "transfer" and tree.kind_of("income.salary") == "income"
    )
    text = tree.render(max_depth=2)
    assert "food.groceries — Groceries" in text and "transport.car.fuel" not in text


def test_agents_add_levels_two_to_five_only(kenv):
    with kenv.db.transaction() as conn:
        bakery = kenv.categories.create_in(
            conn, parent_id="food.eating-out", label="Bakeries", source="agent"
        )
    assert bakery.id == "food.eating-out.bakeries" and bakery.level == 3
    assert bakery.kind == "spend" and bakery.source == "agent"
    with pytest.raises(InputError, match="levels 2 to 5"), kenv.db.transaction() as conn:
        kenv.categories.create_in(conn, parent_id=None, label="Hobby horses", source="agent")
    with pytest.raises(InputError, match="already"):
        kenv.categories.create(parent_id="food.eating-out", label="bakeries")
    mine = kenv.categories.create(parent_id=None, label="Side project", kind="spend")
    assert mine.id == "side-project" and mine.level == 1
    assert kenv.versions.current() == 2  # each new category bumps the knowledge version


def test_render_tree_from_snapshot():
    tree = CategoryTree([])
    assert tree.render() == "" and tree.roots() == []
