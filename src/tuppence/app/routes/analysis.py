"""The analysis run's status, "Run now", and the Home page's summary cards (spec §13 Home)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from tuppence.app.deps import get_services
from tuppence.app.routes.commitments import DueView, upcoming
from tuppence.app.routes.understanding import resolve_period
from tuppence.app.services import Services
from tuppence.core.money import format_pounds
from tuppence.ingest.handoff import ANALYSIS_JOB
from tuppence.knowledge.spending import SpendingFilter, breakdown

Svc = Annotated[Services, Depends(get_services)]
router = APIRouter(prefix="/api", tags=["analysis"])


class AnalysisStatus(BaseModel):
    running: bool
    queued: bool
    last_run: dict[str, Any] | None
    waiting: dict[str, int]


class HomeSummary(BaseModel):
    period: dict[str, Any]
    spent: str
    top: list[dict[str, str]]
    due_soon: list[DueView]
    analysis: AnalysisStatus


def status(services: Services) -> AnalysisStatus:
    running = any(j.kind == ANALYSIS_JOB for j in services.queue.list(status="running"))
    queued = any(j.kind == ANALYSIS_JOB for j in services.queue.list(status="queued"))
    runs = services.analysis.runs(limit=1)
    last = None
    if runs:
        r = runs[0]
        last = {
            "id": r.id,
            "status": r.status,
            "summary": r.summary,
            "finished_at": r.finished_at,
            "started_at": r.started_at,
            "llm_calls": r.llm_calls,
            "cost_gbp": round(r.cost_gbp, 4),
        }
    return AnalysisStatus(
        running=running, queued=queued, last_run=last, waiting=services.analysis.waiting()
    )


@router.get("/analysis")
def get_status(services: Svc) -> AnalysisStatus:
    return status(services)


@router.post("/analysis/run", status_code=202)
def run_now(services: Svc) -> dict[str, int]:
    return {"job_id": services.analysis.request("requested", now=True)}


@router.get("/home/summary")
def home_summary(services: Svc) -> HomeSummary:
    _, period, _ = resolve_period(services, None)
    with services.db.connection() as conn:
        b = breakdown(conn, services.categories.tree(), SpendingFilter(period.start, period.end))
    today = dt.date.today()
    active = [c for c in services.commitments.list() if c.status == "active"]
    return HomeSummary(
        period={"start": period.start, "end": period.end, "label": period.label},
        spent=format_pounds(b.total_pence),
        top=[
            {"id": t.id, "label": t.label, "amount": format_pounds(t.amount_pence)}
            for t in b.tiles[:3]
        ],
        due_soon=upcoming(active, today, today + dt.timedelta(days=6)),
        analysis=status(services),
    )
