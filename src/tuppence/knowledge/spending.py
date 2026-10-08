"""Where the money went (spec §13 Spending): totals by category for a period, at any level
of the tree, and the transactions behind them. Transfers, income and ignored payments are
left out of spending; refunds reduce the spending of the category they were filed in."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from tuppence.knowledge.categories import CategoryTree

UNSORTED = "unsorted"  # the tile for spending that isn't in a category yet
UNSORTED_LABEL = "Not sorted yet"


@dataclass(frozen=True)
class SpendingFilter:
    start: date
    end: date
    account_id: str | None = None
    who: str | None = None  # a person id or "household"
    status: Literal["unknown", "guessed"] | None = None

    def sql(self) -> tuple[str, list[Any]]:
        clauses = ["t.date BETWEEN ? AND ?", "u.ignored = 0"]
        params: list[Any] = [self.start.isoformat(), self.end.isoformat()]
        if self.account_id:
            clauses.append("t.account_id = ?")
            params.append(self.account_id)
        if self.who:
            clauses.append("u.who = ?")
            params.append(self.who)
        if self.status:
            clauses.append("u.status = ?")
            params.append(self.status)
        return " AND ".join(clauses), params


class Crumb(BaseModel):
    id: str | None
    label: str


class Tile(BaseModel):
    id: str  # a category id, or "unsorted"
    label: str
    amount_pence: int  # spending, as a positive number
    count: int
    has_children: bool


class Breakdown(BaseModel):
    path: list[Crumb]
    total_pence: int
    tiles: list[Tile]
    direct_pence: int  # spending filed exactly at this category, not in a child
    money_in_pence: int  # income in the period (whole household view only)
    saved_pence: int  # moved to savings & investments outside Tuppence's accounts
    moved_pence: int = 0  # money out to the household's own accounts, or taken as cash


class TxnRow(BaseModel):
    id: str
    date: date
    amount_pence: int
    description: str
    merchant: str | None
    account_id: str
    category_id: str | None
    category_label: str | None
    who: str | None
    status: str
    decided_by: str | None
    confidence: float
    version: int


def _unsorted(conn: sqlite3.Connection, where: str, params: list[Any]) -> tuple[int, int]:
    """Money out with no category yet (money in with none is not spending)."""
    row = conn.execute(
        "SELECT COALESCE(-SUM(t.amount_pence), 0) AS pence, COUNT(*) AS n"  # noqa: S608
        ' FROM "transaction" t JOIN understanding u ON u.transaction_id = t.id'
        f" WHERE {where} AND u.category_id IS NULL AND t.amount_pence < 0",
        params,
    ).fetchone()
    return int(row["pence"]), int(row["n"])


def breakdown(
    conn: sqlite3.Connection, tree: CategoryTree, f: SpendingFilter, category_id: str | None = None
) -> Breakdown:
    where, params = f.sql()
    sums = conn.execute(
        "SELECT u.category_id, SUM(t.amount_pence) AS pence, COUNT(*) AS n,"  # noqa: S608
        " -SUM(MIN(t.amount_pence, 0)) AS out_pence"
        ' FROM "transaction" t JOIN understanding u ON u.transaction_id = t.id'
        f" WHERE {where} GROUP BY u.category_id",
        params,
    ).fetchall()
    if category_id == UNSORTED:  # the "Not sorted yet" tile: one flat list, nothing under it
        pence, _ = _unsorted(conn, where, params)
        return Breakdown(
            path=[Crumb(id=None, label="All spending"), Crumb(id=UNSORTED, label=UNSORTED_LABEL)],
            total_pence=pence,
            tiles=[],
            direct_pence=pence,
            money_in_pence=0,
            saved_pence=0,
        )
    level = 0 if category_id is None else len(tree.path(category_id))
    tiles: dict[str, list[int]] = {}
    direct = [0, 0]
    money_in = saved = moved = 0
    for r in sums:
        cid, pence, n = r["category_id"], r["pence"], r["n"]
        if cid is None:
            continue
        kind = tree.kind_of(cid)
        if kind == "income":
            money_in += pence
            continue
        if kind == "transfer":
            if cid == "savings" or cid.startswith("savings."):
                saved -= pence
            else:  # both sides of a transfer net to nothing: count the money that left
                moved += r["out_pence"]
            continue  # money moving between the household's own accounts isn't spending
        path = tree.path(cid)
        if category_id is not None and (len(path) < level or path[level - 1].id != category_id):
            continue
        if len(path) == level:
            direct[0] -= pence
            direct[1] += n
            continue
        child = path[level]
        bucket = tiles.setdefault(child.id, [0, 0])
        bucket[0] -= pence
        bucket[1] += n
    out = [
        Tile(
            id=cid,
            label=tree.by_id[cid].label,
            amount_pence=v[0],
            count=v[1],
            has_children=bool(tree.children(cid)),
        )
        for cid, v in tiles.items()
        if v[0] > 0
    ]
    unsorted = _unsorted(conn, where, params) if category_id is None else (0, 0)
    if unsorted[0] > 0:
        out.append(
            Tile(
                id=UNSORTED,
                label=UNSORTED_LABEL,
                amount_pence=unsorted[0],
                count=unsorted[1],
                has_children=False,
            )
        )
    out.sort(key=lambda t: (-t.amount_pence, t.label))
    crumbs = [Crumb(id=None, label="All spending")] + [
        Crumb(id=c.id, label=c.label) for c in (tree.path(category_id) if category_id else [])
    ]
    total = sum(t.amount_pence for t in out) + max(0, direct[0])
    return Breakdown(
        path=crumbs,
        total_pence=total,
        tiles=out,
        direct_pence=max(0, direct[0]),
        money_in_pence=money_in if category_id is None else 0,
        saved_pence=max(0, saved) if category_id is None else 0,
        moved_pence=moved if category_id is None else 0,
    )


def transactions(
    conn: sqlite3.Connection,
    tree: CategoryTree,
    f: SpendingFilter,
    *,
    category_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[TxnRow]:
    """The transactions under a category (and its children), or the unsorted ones."""
    where, params = f.sql()
    if category_id == UNSORTED:
        where += " AND u.category_id IS NULL AND t.amount_pence < 0"
    elif category_id is not None:
        where += " AND u.category_id IN (SELECT value FROM json_each(?))"
        params.append(json.dumps(sorted(tree.descendants(category_id))))
    rows = conn.execute(
        "SELECT t.id, t.date, t.amount_pence, t.raw_description, t.account_id,"  # noqa: S608
        " u.category_id, u.who, u.status, u.decided_by, u.confidence, u.version,"
        ' m.name AS merchant FROM "transaction" t JOIN understanding u'
        " ON u.transaction_id = t.id LEFT JOIN merchant m ON m.id = u.merchant_id"
        f" WHERE {where} ORDER BY t.date DESC, t.id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [
        TxnRow(
            id=r["id"],
            date=date.fromisoformat(r["date"]),
            amount_pence=r["amount_pence"],
            description=r["raw_description"],
            merchant=r["merchant"],
            account_id=r["account_id"],
            category_id=r["category_id"],
            category_label=" › ".join(c.label for c in tree.path(r["category_id"]))
            if r["category_id"]
            else None,
            who=r["who"],
            status=r["status"],
            decided_by=r["decided_by"],
            confidence=r["confidence"],
            version=r["version"],
        )
        for r in rows
    ]


def latest_date(conn: sqlite3.Connection) -> date | None:
    row = conn.execute('SELECT MAX(date) FROM "transaction"').fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None
