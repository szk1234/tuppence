from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from tuppence.app.deps import get_services, require_admin
from tuppence.app.services import Services
from tuppence.core.onboarding import OnboardingState

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


class StepIn(BaseModel):
    status: Literal["done", "skipped"]


@router.get("")
def get_state(services: Svc) -> OnboardingState:
    return services.onboarding.state()


@router.post("/steps/{step}")
def mark_step(step: str, body: StepIn, services: Svc) -> OnboardingState:
    return services.onboarding.mark(step, body.status)


@router.post("/reset", dependencies=[Depends(require_admin)])
def reset(services: Svc) -> OnboardingState:
    return services.onboarding.reset()
