from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ValidationError

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.errors import InputError
from tuppence.core.income import Income, IncomeIn

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/income", tags=["income"])


class IncomeUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class VersionOnly(BaseModel):
    expected_version: int


class RulePreview(BaseModel):
    pay_rule: dict[str, Any]


_PLAIN = {
    "name": "Give this income a name of up to 60 characters.",
    "kind": "Choose what kind of income this is.",
    "variable_components": "Variable pay can be bonus, overtime or commission.",
}


def _input_error(exc: ValidationError) -> InputError:
    err = exc.errors()[0]
    field = ".".join(str(p) for p in err["loc"])
    plain = _PLAIN.get(str(err["loc"][0])) if err["loc"] else None
    if plain and err["type"] != "extra_forbidden":
        return InputError(plain)
    return InputError(f"{field}: {err['msg']}")


@router.get("")
def list_income(services: Svc, include_ended: bool = False) -> dict[str, list[Income]]:
    return {"income": services.income.list(include_ended=include_ended)}


@router.post("", status_code=201)
def create_income(body: dict[str, Any], services: Svc) -> Income:
    try:
        data = IncomeIn.model_validate(body)
    except ValidationError as exc:
        raise _input_error(exc) from None
    return services.income.create(data)


@router.get("/upcoming")
def upcoming(services: Svc, days: Annotated[int, Query(ge=1, le=366)] = 35) -> dict[str, Any]:
    rows = services.income.upcoming(services.income.today(), days)
    return {
        "upcoming": [
            {"income_id": inc.id, "label": inc.name, "date": d, "amount": inc.net_amount}
            for d, inc in rows
        ]
    }


@router.post("/preview-rule")
def preview_rule(body: RulePreview, services: Svc) -> dict[str, Any]:
    description, dates = services.income.preview_rule(body.pay_rule)
    return {"description": description, "next_dates": dates}


@router.patch("/{income_id}")
def update_income(income_id: str, body: IncomeUpdate, services: Svc) -> Income:
    try:
        return services.income.update(income_id, body.changes, body.expected_version)
    except ValidationError as exc:
        raise _input_error(exc) from None


@router.post("/{income_id}/end")
def end_income(income_id: str, body: VersionOnly, services: Svc) -> Income:
    return services.income.end(income_id, body.expected_version)
