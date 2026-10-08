"""The category tree: *what* was bought (spec §7).

Ids are stable dotted slugs (`food.groceries`), so a prompt can name a category and a
model can answer with one. Labels can be renamed; ids never change. The person may add
categories at any level; agents may add levels 2 to 5 only, under a category that exists.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Callable, Sequence
from datetime import datetime

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, update_versioned
from tuppence.knowledge.models import Category, CategoryKind, CategorySource
from tuppence.knowledge.versions import KnowledgeVersions

AGENT_MAX_LEVEL = 5  # the UI shows five levels (spec §7)
MAX_LEVEL = 8  # the person's own limit, so the tree stays usable
LABEL_MAX = 40

# (id, label, kind, essential). Parents come before their children. "essential" marks the
# spending the emergency-fund maths counts (M6).
SEED_CATEGORIES: list[tuple[str, str, CategoryKind, bool]] = [
    ("housing", "Housing", "spend", False),
    ("housing.rent", "Rent", "spend", True),
    ("housing.mortgage", "Mortgage", "spend", True),
    ("housing.council-tax", "Council tax", "spend", True),
    ("housing.water", "Water", "spend", True),
    ("housing.energy", "Gas & electricity", "spend", True),
    ("housing.broadband", "Broadband & home phone", "spend", True),
    ("housing.tv-licence", "TV licence", "spend", True),
    ("housing.insurance", "Home insurance", "spend", True),
    ("housing.repairs", "Repairs & maintenance", "spend", False),
    ("housing.furnishing", "Furniture & household", "spend", False),
    ("transport", "Transport", "spend", False),
    ("transport.car", "Car", "spend", False),
    ("transport.car.finance", "Car finance", "spend", True),
    ("transport.car.insurance", "Car insurance", "spend", True),
    ("transport.car.fuel", "Fuel & charging", "spend", True),
    ("transport.car.road-tax", "Road tax", "spend", True),
    ("transport.car.parking", "Parking & tolls", "spend", False),
    ("transport.car.servicing", "MOT, servicing & repairs", "spend", True),
    ("transport.car.breakdown", "Breakdown cover", "spend", False),
    ("transport.public", "Public transport", "spend", True),
    ("transport.taxi", "Taxis", "spend", False),
    ("food", "Food & drink", "spend", False),
    ("food.groceries", "Groceries", "spend", True),
    ("food.eating-out", "Eating out", "spend", False),
    ("food.takeaway", "Takeaways & delivery", "spend", False),
    ("children", "Children", "spend", False),
    ("children.childcare", "Childcare & nursery", "spend", True),
    ("children.school", "School costs", "spend", True),
    ("children.activities", "Clubs & activities", "spend", False),
    ("children.clothes", "Children's clothes", "spend", False),
    ("children.toys", "Toys & books", "spend", False),
    ("children.pocket-money", "Pocket money", "spend", False),
    ("health", "Health", "spend", False),
    ("health.pharmacy", "Pharmacy & prescriptions", "spend", True),
    ("health.dental", "Dentist", "spend", True),
    ("health.optician", "Optician", "spend", False),
    ("health.fitness", "Gym & fitness", "spend", False),
    ("health.insurance", "Health insurance", "spend", False),
    ("personal-care", "Personal care", "spend", False),
    ("personal-care.hair", "Hair & beauty", "spend", False),
    ("personal-care.toiletries", "Toiletries", "spend", True),
    ("clothing", "Clothes & shoes", "spend", False),
    ("clothing.clothes", "Clothes", "spend", False),
    ("clothing.shoes", "Shoes", "spend", False),
    ("entertainment", "Entertainment", "spend", False),
    ("entertainment.going-out", "Cinema, gigs & going out", "spend", False),
    ("entertainment.hobbies", "Hobbies", "spend", False),
    ("entertainment.days-out", "Days out", "spend", False),
    ("entertainment.games", "Games", "spend", False),
    ("subscriptions", "Subscriptions", "spend", False),
    ("subscriptions.tv-streaming", "TV & video streaming", "spend", False),
    ("subscriptions.music", "Music streaming", "spend", False),
    ("subscriptions.software", "Apps, software & cloud storage", "spend", False),
    ("subscriptions.news", "News & magazines", "spend", False),
    ("subscriptions.mobile", "Mobile phone", "spend", True),
    ("subscriptions.memberships", "Memberships", "spend", False),
    ("holidays", "Holidays & travel", "spend", False),
    ("holidays.travel", "Flights, trains & ferries", "spend", False),
    ("holidays.accommodation", "Accommodation", "spend", False),
    ("holidays.spending", "Spending abroad", "spend", False),
    ("gifts", "Gifts & celebrations", "spend", False),
    ("gifts.presents", "Presents", "spend", False),
    ("gifts.celebrations", "Parties & celebrations", "spend", False),
    ("charity", "Charity", "spend", False),
    ("pets", "Pets", "spend", False),
    ("pets.food", "Pet food & supplies", "spend", True),
    ("pets.vet", "Vet", "spend", True),
    ("pets.insurance", "Pet insurance", "spend", False),
    ("education", "Education", "spend", False),
    ("education.courses", "Courses & tuition", "spend", False),
    ("education.books", "Books & materials", "spend", False),
    ("financial", "Financial costs", "spend", False),
    ("financial.bank-fees", "Bank & card fees", "spend", True),
    ("financial.interest", "Interest charges", "spend", True),
    ("financial.loan-repayments", "Loan repayments", "spend", True),
    ("financial.protection", "Life & income protection", "spend", True),
    ("financial.tax", "Tax payments", "spend", True),
    ("business", "Business costs", "spend", False),
    ("business.supplies", "Supplies & equipment", "spend", False),
    ("business.software", "Business software", "spend", False),
    ("business.travel", "Business travel", "spend", False),
    ("other", "Other spending", "spend", False),
    ("income", "Income", "income", False),
    ("income.salary", "Salary & wages", "income", False),
    ("income.benefits", "Benefits", "income", False),
    ("income.pension", "Pension income", "income", False),
    ("income.refunds", "Refunds", "income", False),
    ("income.interest", "Interest earned", "income", False),
    ("income.from-others", "Money from family & friends", "income", False),
    ("income.side", "Side income & sales", "income", False),
    ("income.other", "Other income", "income", False),
    ("savings", "Savings & investments", "transfer", False),
    ("savings.investments", "Investments", "transfer", False),
    ("savings.pension", "Pension contributions", "transfer", False),
    ("transfers", "Transfers", "transfer", False),
    ("transfers.between-accounts", "Between your accounts", "transfer", False),
    ("transfers.card-repayment", "Credit card repayments", "transfer", False),
    ("transfers.cash", "Cash withdrawals", "transfer", False),
]


def slugify(label: str) -> str:
    text = unicodedata.normalize("NFKD", label)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


class CategoryTree:
    """An immutable snapshot of every category, retired ones included."""

    def __init__(self, categories: Sequence[Category]) -> None:
        self.by_id = {c.id: c for c in categories}
        self._children: dict[str | None, list[Category]] = {}
        for c in sorted(categories, key=lambda c: (c.sort_order, c.id)):
            self._children.setdefault(c.parent_id, []).append(c)

    def get(self, category_id: str | None) -> Category | None:
        return self.by_id.get(category_id) if category_id else None

    def usable(self, category_id: str | None) -> bool:
        found = self.get(category_id)
        return found is not None and not found.retired

    def children(self, category_id: str | None, *, include_retired: bool = False) -> list[Category]:
        found = self._children.get(category_id, [])
        return found if include_retired else [c for c in found if not c.retired]

    def roots(self) -> list[Category]:
        return self.children(None)

    def path(self, category_id: str) -> list[Category]:
        """Root first, ending with the category itself."""
        out: list[Category] = []
        current = self.get(category_id)
        while current is not None:
            out.append(current)
            current = self.get(current.parent_id)
        return list(reversed(out))

    def ancestor_at(self, category_id: str | None, level: int) -> str | None:
        """The id of the category's ancestor at `level` (itself when it is at that level)."""
        if not category_id or self.get(category_id) is None:
            return None
        path = self.path(category_id)
        return path[level - 1].id if len(path) >= level else None

    def descendants(self, category_id: str) -> set[str]:
        """The category and everything under it."""
        out, stack = set(), [category_id]
        while stack:
            current = stack.pop()
            out.add(current)
            stack.extend(c.id for c in self._children.get(current, []))
        return out

    def kind_of(self, category_id: str | None) -> CategoryKind | None:
        found = self.get(category_id)
        return found.kind if found else None

    def render(self, *, max_depth: int = AGENT_MAX_LEVEL) -> str:
        """The active tree as prompt text: one `id — label` per line, indented by level."""
        lines: list[str] = []

        def walk(parent: str | None) -> None:
            for c in self.children(parent):
                if c.level <= max_depth:
                    lines.append(f"{'  ' * (c.level - 1)}{c.id} — {c.label}")
                    walk(c.id)

        walk(None)
        return "\n".join(lines)


def _category(row: sqlite3.Row) -> Category:
    data = dict(row)
    data["essential"] = bool(data["essential"])
    data["retired"] = bool(data["retired"])
    return Category.model_validate(data)


class CategoryStore:
    def __init__(
        self, db: Database, versions: KnowledgeVersions, *, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self.db, self.versions, self.clock = db, versions, clock

    def seed(self) -> int:
        """Add any missing starter categories. A retired seed category stays retired."""
        now = to_iso(self.clock())
        added = 0
        with self.db.transaction() as conn:
            for order, (cid, label, kind, essential) in enumerate(SEED_CATEGORIES):
                parent = cid.rsplit(".", 1)[0] if "." in cid else None
                cur = conn.execute(
                    "INSERT OR IGNORE INTO category (id, parent_id, level, label, kind, essential,"
                    " source, sort_order, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'seed', ?, ?, ?)",
                    [cid, parent, cid.count(".") + 1, label, kind, int(essential), order, now, now],
                )
                added += cur.rowcount
        return added

    def tree(self) -> CategoryTree:
        with self.db.connection() as conn:
            return CategoryTree([_category(r) for r in conn.execute("SELECT * FROM category")])

    def get(self, category_id: str) -> Category:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM category WHERE id = ?", [category_id]).fetchone()
        if row is None:
            raise NotFound("category", category_id)
        return _category(row)

    def create_in(
        self,
        conn: sqlite3.Connection,
        *,
        parent_id: str | None,
        label: str,
        source: CategorySource,
        kind: CategoryKind | None = None,
        essential: bool | None = None,
    ) -> Category:
        """Add a category inside the caller's transaction and bump the knowledge version."""
        label = " ".join(label.split())
        if not 1 <= len(label) <= LABEL_MAX:
            raise InputError(f"A category name is 1 to {LABEL_MAX} characters.")
        slug = slugify(label)
        if not slug:
            raise InputError("A category name needs at least one letter or number.")
        parent = None
        if parent_id is not None:
            row = conn.execute("SELECT * FROM category WHERE id = ?", [parent_id]).fetchone()
            if row is None or row["retired"]:
                raise InputError("That parent category doesn't exist.")
            parent = _category(row)
        if source == "agent" and (parent is None or parent.level + 1 > AGENT_MAX_LEVEL):
            raise InputError("Tuppence can only add categories at levels 2 to 5.")
        level = 1 if parent is None else parent.level + 1
        if level > MAX_LEVEL:
            raise InputError(f"Categories can be at most {MAX_LEVEL} levels deep.")
        if parent is None and kind is None:
            raise InputError(
                "Choose whether a top-level category is spending, income or a transfer."
            )
        new_id = slug if parent is None else f"{parent.id}.{slug}"
        if conn.execute("SELECT 1 FROM category WHERE id = ?", [new_id]).fetchone():
            raise InputError(f"There is already a category called {label} there.")
        now = to_iso(self.clock())
        conn.execute(
            "INSERT INTO category (id, parent_id, level, label, kind, essential, source,"
            " sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1000, ?, ?)",
            [
                new_id,
                parent_id,
                level,
                label,
                kind or (parent.kind if parent else "spend"),
                int(
                    essential if essential is not None else (parent.essential if parent else False)
                ),
                source,
                now,
                now,
            ],
        )
        self.versions.bump(conn, "category", category_id=new_id, note=f"added {label}")
        return _category(conn.execute("SELECT * FROM category WHERE id = ?", [new_id]).fetchone())

    def create(
        self,
        *,
        parent_id: str | None,
        label: str,
        source: CategorySource = "user",
        kind: CategoryKind | None = None,
        essential: bool | None = None,
    ) -> Category:
        with self.db.transaction() as conn:
            return self.create_in(
                conn,
                parent_id=parent_id,
                label=label,
                source=source,
                kind=kind,
                essential=essential,
            )

    def update(
        self,
        category_id: str,
        expected_version: int,
        *,
        label: str | None = None,
        essential: bool | None = None,
    ) -> Category:
        changes: dict[str, object] = {}
        if label is not None:
            label = " ".join(label.split())
            if not 1 <= len(label) <= LABEL_MAX:
                raise InputError(f"A category name is 1 to {LABEL_MAX} characters.")
            changes["label"] = label
        if essential is not None:
            changes["essential"] = int(essential)
        if not changes:
            return self.get(category_id)
        with self.db.transaction() as conn:
            update_versioned(
                conn,
                "category",
                "id",
                category_id,
                expected_version,
                changes,
                now=to_iso(self.clock()),
            )
        return self.get(category_id)

    def retire(self, category_id: str, expected_version: int) -> list[str]:
        """Retire a category and everything under it (the person's action).

        Their transactions become stale, so the next analysis files them again."""
        tree = self.tree()
        if tree.get(category_id) is None:
            raise NotFound("category", category_id)
        ids = sorted(tree.descendants(category_id))
        with self.db.transaction() as conn:
            update_versioned(
                conn,
                "category",
                "id",
                category_id,
                expected_version,
                {"retired": 1},
                now=to_iso(self.clock()),
            )
            for cid in ids:
                conn.execute("UPDATE category SET retired = 1 WHERE id = ?", [cid])
                self.versions.bump(conn, "category", category_id=cid, note="retired")
        return ids
