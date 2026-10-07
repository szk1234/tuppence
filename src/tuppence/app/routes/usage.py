from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.errors import InputError

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("")
def usage(services: Svc, month: str | None = None) -> dict[str, Any]:
    if month:
        try:
            year, mon = (int(x) for x in month.split("-"))
            date(year, mon, 1)
        except ValueError:
            raise InputError("Use the month format YYYY-MM.") from None
    else:
        today = date.today()
        year, mon = today.year, today.month
    out = services.usage.summary(year, mon)
    out["month"] = f"{year:04d}-{mon:02d}"
    out["cap_gbp"] = services.settings.get("llm.monthly_cap_gbp")
    return out
