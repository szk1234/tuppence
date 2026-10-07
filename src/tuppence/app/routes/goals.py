from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.errors import InputError
from tuppence.core.goals import Goal, GoalIn

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/goals", tags=["goals"])


class GoalUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class StatusChange(BaseModel):
    status: str
    expected_version: int


_PLAIN = {
    "name": "Give this goal a name of up to 60 characters.",
    "kind": "Choose what kind of goal this is.",
    "target_amount": "Enter the target as pounds, like 5000.",
    "saved_amount": "Enter what you've saved as pounds, like 250.50.",
    "target_date": "Enter the target date like 2027-06-30.",
    "priority": "Priority is 1 (highest), 2 or 3.",
}


def _input_error(exc: ValidationError) -> InputError:
    err = exc.errors()[0]
    field = ".".join(str(p) for p in err["loc"])
    plain = _PLAIN.get(str(err["loc"][0])) if err["loc"] else None
    return InputError(plain or f"{field}: {err['msg']}")


@router.get("")
def list_goals(services: Svc, include_closed: bool = False) -> dict[str, list[Goal]]:
    return {"goals": services.goals.list(include_closed=include_closed)}


@router.post("", status_code=201)
def create_goal(body: dict[str, Any], services: Svc) -> Goal:
    try:
        data = GoalIn.model_validate(body)
    except ValidationError as exc:
        raise _input_error(exc) from None
    return services.goals.create(data)


@router.get("/suggestions")
def suggestions(services: Svc) -> dict[str, bool]:
    return {"emergency_fund": services.goals.suggest_emergency_fund()}


@router.patch("/{goal_id}")
def update_goal(goal_id: str, body: GoalUpdate, services: Svc) -> Goal:
    try:
        return services.goals.update(goal_id, body.changes, body.expected_version)
    except ValidationError as exc:
        raise _input_error(exc) from None


@router.post("/{goal_id}/status")
def goal_status(goal_id: str, body: StatusChange, services: Svc) -> Goal:
    return services.goals.set_status(goal_id, body.status, body.expected_version)
