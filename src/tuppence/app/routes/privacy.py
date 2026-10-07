from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.net.privacy_log import PrivacyLogEntry

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


@router.get("/log")
def privacy_log(
    services: Svc,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before_id: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
) -> dict[str, list[PrivacyLogEntry]]:
    return {"entries": services.privacy_log.list(limit=limit, before_id=before_id)}
