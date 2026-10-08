"""Commitments: bills, subscriptions and instalments, their calendar and flags (spec §13)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from tuppence.app.deps import get_services
from tuppence.app.routes._ids import ExpectedVersion, PathId
from tuppence.app.services import Services
from tuppence.core.money import format_pounds
from tuppence.knowledge.commitments import Commitment, project

Svc = Annotated[Services, Depends(get_services)]
router = APIRouter(prefix="/api/commitments", tags=["commitments"])
FLAG_LABELS = {
    "price_rise": "Price went up",
    "missed": "A payment was missed",
    "lapsed": "Stopped",
    "duplicate": "Possible duplicate",
    "free_trial_converted": "Free trial turned into a paid plan",
    "varies": "Amount varies",
}
CADENCE_LABELS = {
    "weekly": "Every week",
    "fortnightly": "Every 2 weeks",
    "four_weekly": "Every 4 weeks",
    "monthly": "Every month",
    "quarterly": "Every 3 months",
    "annual": "Every year",
}


class CommitmentView(BaseModel):
    id: str
    name: str
    kind: str
    cadence: str
    cadence_label: str
    amount: str
    annual_cost: str
    next_due: dt.date | None
    last_paid: dt.date
    status: str
    flags: list[str]
    flag_labels: list[str]
    price_history: list[dict[str, str]]
    duplicate_of: list[str]
    account_id: str
    category_id: str | None
    dismissed: bool
    version: int


class DueView(BaseModel):
    date: dt.date
    commitment_id: str
    name: str
    amount: str
    kind: str


class Totals(BaseModel):
    annual: str
    monthly: str
    count: int
    by_kind: dict[str, str]


class CommitmentsView(BaseModel):
    commitments: list[CommitmentView]
    totals: Totals
    upcoming: list[DueView]
    calendar_start: dt.date
    calendar_end: dt.date


class VersionIn(BaseModel):
    expected_version: ExpectedVersion


def view(c: Commitment) -> CommitmentView:
    return CommitmentView(
        id=c.id,
        name=c.name,
        kind=c.kind,
        cadence=c.cadence,
        cadence_label=CADENCE_LABELS[c.cadence],
        amount=format_pounds(c.expected_amount_pence),
        annual_cost=format_pounds(c.annual_cost_pence),
        next_due=c.next_due,
        last_paid=c.last_date,
        status=c.status,
        flags=c.flags,
        flag_labels=[FLAG_LABELS[f] for f in c.flags],
        price_history=[
            {"since": p["since"], "amount": format_pounds(p["amount_pence"])}
            for p in c.price_history
        ],
        duplicate_of=list(c.evidence.get("duplicate_of", [])),
        account_id=c.account_id,
        category_id=c.category_id,
        dismissed=c.dismissed,
        version=c.version,
    )


def upcoming(items: list[Commitment], start: dt.date, end: dt.date) -> list[DueView]:
    dues = [
        DueView(
            date=day,
            commitment_id=c.id,
            name=c.name,
            amount=format_pounds(c.expected_amount_pence),
            kind=c.kind,
        )
        for c in items
        for day in project(c, start, end)
    ]
    return sorted(dues, key=lambda d: (d.date, d.name))


@router.get("")
def list_commitments(
    services: Svc,
    include_dismissed: bool = False,
    start: dt.date | None = None,
    days: Annotated[int, Query(ge=1, le=400)] = 62,
) -> CommitmentsView:
    items = services.commitments.list(include_dismissed=include_dismissed)
    active = [c for c in items if c.status == "active" and not c.dismissed]
    annual = sum(c.annual_cost_pence for c in active)
    by_kind = {
        k: format_pounds(sum(c.annual_cost_pence for c in active if c.kind == k))
        for k in ("bill", "subscription", "instalment")
    }
    first = start or dt.date.today()
    return CommitmentsView(
        commitments=[view(c) for c in items],
        totals=Totals(
            annual=format_pounds(annual),
            monthly=format_pounds(round(annual / 12)),
            count=len(active),
            by_kind=by_kind,
        ),
        upcoming=upcoming(active, first, first + dt.timedelta(days=days - 1)),
        calendar_start=first,
        calendar_end=first + dt.timedelta(days=days - 1),
    )


@router.post("/{commitment_id}/dismiss")
def dismiss(commitment_id: PathId, body: VersionIn, services: Svc) -> CommitmentView:
    """'This isn't a commitment': hidden, and not found again for this merchant and account."""
    return view(services.commitments.set_dismissed(commitment_id, body.expected_version, True))


@router.post("/{commitment_id}/restore")
def restore(commitment_id: PathId, body: VersionIn, services: Svc) -> CommitmentView:
    restored = services.commitments.set_dismissed(commitment_id, body.expected_version, False)
    services.analysis.request("commitment_restored")
    return view(restored)
