from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.net.privacy_log import PrivacyLogEntry

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


@router.get("/log")
def privacy_log(
    services: Svc, limit: int = 100, before_id: int | None = None
) -> dict[str, list[PrivacyLogEntry]]:
    return {"entries": services.privacy_log.list(limit=limit, before_id=before_id)}
