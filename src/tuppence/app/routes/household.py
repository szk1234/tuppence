from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.errors import InputError
from tuppence.core.household import Household, HouseholdPatch, Person, PersonIn, PersonPatch
from tuppence.core.timeline import TimelineEntry

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/household", tags=["household"])


class HouseholdUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class PersonUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class VersionOnly(BaseModel):
    expected_version: int


class TimelineIn(BaseModel):
    subject_type: str
    subject_id: str
    attribute: str
    value: Any
    valid_from: date


def _parse(model: type[BaseModel], data: dict[str, Any]) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        field = ".".join(str(p) for p in err["loc"])
        raise InputError(f"{field}: {err['msg']}") from None


@router.get("")
def get_household(services: Svc) -> Household:
    return services.household.get()


@router.patch("")
def update_household(body: HouseholdUpdate, services: Svc) -> Household:
    return services.household.update(_parse(HouseholdPatch, body.changes), body.expected_version)


@router.get("/people")
def list_people(services: Svc, include_retired: bool = False) -> dict[str, list[Person]]:
    return {"people": services.household.list_people(include_retired=include_retired)}


@router.post("/people", status_code=201)
def create_person(body: dict[str, Any], services: Svc) -> Person:
    return services.household.create_person(_parse(PersonIn, body))


@router.patch("/people/{person_id}")
def update_person(person_id: str, body: PersonUpdate, services: Svc) -> Person:
    return services.household.update_person(
        person_id, _parse(PersonPatch, body.changes), body.expected_version
    )


@router.post("/people/{person_id}/retire")
def retire_person(person_id: str, body: VersionOnly, services: Svc) -> Person:
    return services.household.retire_person(person_id, body.expected_version)


@router.get("/timeline")
def timeline(subject_type: str, subject_id: str, services: Svc) -> dict[str, list[TimelineEntry]]:
    return {"entries": services.timeline.history(subject_type, subject_id)}


@router.post("/timeline", status_code=201)
def add_timeline(body: TimelineIn, services: Svc) -> TimelineEntry:
    return services.timeline.set(
        body.subject_type, body.subject_id, body.attribute, body.value, body.valid_from
    )
