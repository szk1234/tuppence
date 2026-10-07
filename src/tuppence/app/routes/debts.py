from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.debts import Debt, DebtIn
from tuppence.core.errors import InputError

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/debts", tags=["debts"])


class DebtUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class VersionOnly(BaseModel):
    expected_version: int


_PLAIN = {
    "kind": "Choose what kind of debt this is.",
    "lender": "Give the lender or person a name of up to 60 characters.",
    "balance": "Enter the balance as pounds, like 1234.50.",
    "balance_date": "Enter the balance date like 2026-10-07.",
    "apr": "Enter an APR between 0 and 100, with at most 2 decimal places.",
    "monthly_payment": "Enter the monthly payment as pounds, like 250.00.",
    "end_date": "Enter the end date like 2030-01-31.",
    "student_loan_plan": "Choose a student loan plan.",
    "person_id": "Choose a person from your household, or leave it as joint.",
    "details": "Those extra details aren't valid.",
}


def _input_error(exc: ValidationError) -> InputError:
    err = exc.errors()[0]
    field = ".".join(str(p) for p in err["loc"])
    plain = _PLAIN.get(str(err["loc"][0])) if err["loc"] else None
    return InputError(plain or f"{field}: {err['msg']}")


@router.get("")
def list_debts(services: Svc, include_settled: bool = False) -> dict[str, list[Debt]]:
    return {"debts": services.debts.list(include_settled=include_settled)}


@router.post("", status_code=201)
def create_debt(body: dict[str, Any], services: Svc) -> Debt:
    if "balance_date" not in body:
        body = {**body, "balance_date": services.debts.today().isoformat()}
    try:
        data = DebtIn.model_validate(body)
    except ValidationError as exc:
        raise _input_error(exc) from None
    return services.debts.create(data)


@router.patch("/{debt_id}")
def update_debt(debt_id: str, body: DebtUpdate, services: Svc) -> Debt:
    try:
        return services.debts.update(debt_id, body.changes, body.expected_version)
    except ValidationError as exc:
        raise _input_error(exc) from None


@router.post("/{debt_id}/settle")
def settle_debt(debt_id: str, body: VersionOnly, services: Svc) -> Debt:
    return services.debts.settle(debt_id, body.expected_version)
