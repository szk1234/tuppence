from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.clock import to_iso

Svc = Annotated[Services, Depends(get_services)]
router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(services: Svc, status: str | None = None) -> dict[str, list[dict[str, Any]]]:
    return {
        "jobs": [
            {
                "id": j.id,
                "kind": j.kind,
                "scope_key": j.scope_key,
                "status": j.status,
                "attempts": j.attempts,
                "run_after": to_iso(j.run_after),
                "error": j.error,
            }
            for j in services.queue.list(status=status)
        ]
    }
