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
    now = services.usage.clock()  # UTC: the same clock the monthly cap uses
    year, mon = now.year, now.month
    if month:
        try:
            year, mon = (int(x) for x in month.split("-"))
            date(year, mon, 1)
            if not 2000 <= year <= 2100:
                raise ValueError(year)
        except ValueError:
            raise InputError("Use the month format YYYY-MM.") from None
    out = services.usage.summary(year, mon)
    out["month"] = f"{year:04d}-{mon:02d}"
    out["current_month"] = f"{now.year:04d}-{now.month:02d}"
    out["cap_gbp"] = services.settings.get("llm.monthly_cap_gbp")
    return out
