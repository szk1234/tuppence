"""Spending, the transactions behind it, the "Why?" panel and the person's corrections
(spec §13 Spending, §10.2)."""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from tuppence.app.deps import get_services
from tuppence.app.routes._ids import BodyId, ExpectedVersion, PathId, QueryId
from tuppence.app.services import Services
from tuppence.core.errors import InputError
from tuppence.core.money import format_pounds, parse_pounds
from tuppence.core.payrules import parse_rule, pay_dates
from tuppence.core.periods import Period, PeriodRules
from tuppence.core.records import NotFound
from tuppence.core.timeline import HOUSEHOLD_ID
from tuppence.knowledge.categories import CategoryTree
from tuppence.knowledge.models import HOUSEHOLD, Understanding
from tuppence.knowledge.rules import RuleIn
from tuppence.knowledge.spending import (
    UNSORTED,
    SpendingFilter,
    breakdown,
    latest_date,
    transactions,
)
from tuppence.knowledge.why import Why, explain

Svc = Annotated[Services, Depends(get_services)]
router = APIRouter(prefix="/api", tags=["understanding"])
Status = Literal["unknown", "guessed"]


class PeriodView(BaseModel):
    start: dt.date
    end: dt.date
    label: str
    mode: str
    previous: dt.date  # any day in the period before
    next: dt.date  # any day in the period after
    has_later_data: bool


class TileView(BaseModel):
    id: str
    label: str
    amount: str
    count: int
    has_children: bool


class CrumbView(BaseModel):
    id: str | None
    label: str


class SpendingView(BaseModel):
    period: PeriodView
    path: list[CrumbView]
    total: str
    direct: str
    money_in: str
    saved: str
    tiles: list[TileView]
    waiting_for_ai: int


class TxnView(BaseModel):
    id: str
    date: dt.date
    amount: str
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


class UnderstandingView(BaseModel):
    transaction_id: str
    category_id: str | None
    who: str | None
    is_transfer: bool
    ignored: bool
    status: str
    decided_by: str | None
    confidence: float
    version: int


class RuleOffer(BaseModel):
    merchant_id: str
    merchant_name: str
    category_id: str
    matches: int
    will_change: int
    kept_yours: int


class Correction(BaseModel):
    category_id: BodyId | None = None
    who: BodyId | None = None
    is_transfer: bool | None = None
    ignored: bool | None = None
    expected_version: ExpectedVersion


class CorrectionResult(BaseModel):
    understanding: UnderstandingView
    rule_offer: RuleOffer | None


class VersionIn(BaseModel):
    expected_version: ExpectedVersion


def period_rules(services: Services) -> PeriodRules:
    """Calendar months, or pay cycles anchored on the chosen adult's main income (spec §5.4)."""
    household = services.household.get()
    anchor = household.period_anchor_person_id
    if household.period_mode != "pay_cycle" or anchor is None:
        return PeriodRules("calendar_month")
    incomes = [i for i in services.income.list() if i.person_id == anchor]
    if not incomes:
        return PeriodRules("calendar_month")
    main = max(incomes, key=lambda i: (i.kind == "salary", parse_pounds(i.net_amount)))
    rule = parse_rule(main.pay_rule)
    # The nation as of each date (the timeline is the source of truth, M2 ruling R14).
    nation = services.timeline.lookup("household", HOUSEHOLD_ID, "nation")
    return PeriodRules("pay_cycle", lambda start, end: pay_dates(rule, start, end, nation))


def resolve_period(services: Services, on: dt.date | None) -> tuple[PeriodRules, Period, bool]:
    """The period containing `on`; by default the one containing the latest transaction
    (or today, whichever is earlier), so old statements still open on their own data."""
    rules = period_rules(services)
    with services.db.connection() as conn:
        latest = latest_date(conn)
    day = on or min(dt.date.today(), latest or dt.date.today())
    period = rules.period_for(day)
    return rules, period, latest is not None and latest > period.end


def _period_view(rules: PeriodRules, period: Period, later: bool) -> PeriodView:
    return PeriodView(
        start=period.start,
        end=period.end,
        label=period.label,
        mode=period.mode,
        previous=rules.shift(period, -1).start,
        next=rules.shift(period, 1).start,
        has_later_data=later,
    )


def check_category(tree: CategoryTree, category: str | None) -> None:
    """An unknown category is a 404. A retired one still opens: money was filed there."""
    if category is not None and category != UNSORTED and tree.get(category) is None:
        raise NotFound("category", category)


def _understanding(u: Understanding) -> UnderstandingView:
    return UnderstandingView(
        transaction_id=u.transaction_id,
        category_id=u.category_id,
        who=u.who,
        is_transfer=u.is_transfer,
        ignored=u.ignored,
        status=u.status,
        decided_by=u.decided_by,
        confidence=u.confidence,
        version=u.version,
    )


def merchant_linker(
    services: Services, transaction_id: str
) -> Callable[[sqlite3.Connection], str | None]:
    """Gives the row its merchant inside the correction's own transaction. The Categoriser never
    writes a confirmed row, so a row confirmed without one would never feed merchant memory or be
    matched by a merchant rule. If the correction fails, no merchant is left behind."""

    def link(conn: sqlite3.Connection) -> str | None:
        raw = conn.execute(
            'SELECT raw_description, merchant_text FROM "transaction" WHERE id = ?',
            [transaction_id],
        ).fetchone()
        if raw is None:
            return None
        merchant = services.merchants.resolve(conn, raw["raw_description"], raw["merchant_text"])
        return merchant.id if merchant is not None else None

    return link


@router.get("/spending")
def spending(
    services: Svc,
    on: dt.date | None = None,
    category: QueryId = None,
    account_id: QueryId = None,
    who: QueryId = None,
    status: Status | None = None,
) -> SpendingView:
    rules, period, later = resolve_period(services, on)
    f = SpendingFilter(period.start, period.end, account_id=account_id, who=who, status=status)
    tree = services.categories.tree()
    check_category(tree, category)
    with services.db.connection() as conn:
        b = breakdown(conn, tree, f, category)
    waiting = services.analysis.waiting()
    return SpendingView(
        period=_period_view(rules, period, later),
        path=[CrumbView(id=c.id, label=c.label) for c in b.path],
        total=format_pounds(b.total_pence),
        direct=format_pounds(b.direct_pence),
        money_in=format_pounds(b.money_in_pence),
        saved=format_pounds(b.saved_pence),
        tiles=[
            TileView(
                id=t.id,
                label=t.label,
                amount=format_pounds(t.amount_pence),
                count=t.count,
                has_children=t.has_children,
            )
            for t in b.tiles
        ],
        waiting_for_ai=waiting.get("awaiting_ai", 0),
    )


@router.get("/spending/transactions")
def spending_transactions(
    services: Svc,
    on: dt.date | None = None,
    category: QueryId = None,
    account_id: QueryId = None,
    who: QueryId = None,
    status: Status | None = None,
    offset: Annotated[int, Query(ge=0, le=10_000_000)] = 0,
) -> dict[str, list[TxnView]]:
    _, period, _ = resolve_period(services, on)
    f = SpendingFilter(period.start, period.end, account_id=account_id, who=who, status=status)
    tree = services.categories.tree()
    check_category(tree, category)
    with services.db.connection() as conn:
        rows = transactions(conn, tree, f, category_id=category, offset=offset)
    return {
        "transactions": [
            TxnView(**r.model_dump(exclude={"amount_pence"}), amount=format_pounds(r.amount_pence))
            for r in rows
        ]
    }


@router.get("/transactions/{transaction_id}/why")
def why(transaction_id: PathId, services: Svc) -> Why:
    u = services.understanding.get(transaction_id)
    with services.db.connection() as conn:
        return explain(
            conn,
            services.categories.tree(),
            u,
            services.understanding.history(transaction_id),
            services.versions.current(),
        )


@router.patch("/transactions/{transaction_id}/understanding")
def correct(transaction_id: PathId, body: Correction, services: Svc) -> CorrectionResult:
    """The person says what this is. If other payments to the same merchant would change, the
    reply offers a rule to file them the same way."""
    if body.who is not None and body.who != HOUSEHOLD:
        try:
            services.household.get_person(body.who)
        except NotFound:
            raise InputError("Choose someone from your household.") from None
    u = services.understanding.set_by_person(
        transaction_id,
        expected_version=body.expected_version,
        category_id=body.category_id,
        who=body.who,
        is_transfer=body.is_transfer,
        ignored=body.ignored,
        link_merchant=merchant_linker(services, transaction_id),
    )
    services.analysis.request("correction")
    offer = None
    if body.category_id is not None and u.merchant_id is not None:
        preview = services.rules.preview(
            RuleIn(merchant_id=u.merchant_id, set_category_id=body.category_id)
        )
        if preview.will_change > 0:
            offer = RuleOffer(
                merchant_id=u.merchant_id,
                merchant_name=services.merchants.get(u.merchant_id).name,
                category_id=body.category_id,
                matches=preview.matches,
                will_change=preview.will_change,
                kept_yours=preview.kept_yours,
            )
    return CorrectionResult(understanding=_understanding(u), rule_offer=offer)


@router.post("/transactions/{transaction_id}/understanding/reset")
def reset(transaction_id: PathId, body: VersionIn, services: Svc) -> UnderstandingView:
    u = services.understanding.release_by_person(
        transaction_id, expected_version=body.expected_version
    )
    services.analysis.request("correction")
    return _understanding(u)
