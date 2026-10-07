from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.accounts import Account, AccountIn
from tuppence.core.errors import InputError
from tuppence.core.providers_uk import PROVIDERS

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class VersionOnly(BaseModel):
    expected_version: int


def _input_error(exc: ValidationError) -> InputError:
    err = exc.errors()[0]
    field = ".".join(str(p) for p in err["loc"])
    return InputError(f"{field}: {err['msg']}")


@router.get("/providers")
def providers() -> dict[str, list[dict[str, Any]]]:
    return {"providers": [{"id": p.id, "name": p.name, "kinds": list(p.kinds)} for p in PROVIDERS]}


@router.get("")
def list_accounts(services: Svc, include_closed: bool = False) -> dict[str, list[Account]]:
    return {"accounts": services.accounts.list(include_closed=include_closed)}


@router.post("", status_code=201)
def create_account(body: dict[str, Any], services: Svc) -> Account:
    try:
        data = AccountIn.model_validate(body)
    except ValidationError as exc:
        raise _input_error(exc) from None
    return services.accounts.create(data)


@router.patch("/{account_id}")
def update_account(account_id: str, body: AccountUpdate, services: Svc) -> Account:
    try:
        return services.accounts.update(account_id, body.changes, body.expected_version)
    except ValidationError as exc:
        raise _input_error(exc) from None


@router.post("/{account_id}/close")
def close_account(account_id: str, body: VersionOnly, services: Svc) -> Account:
    return services.accounts.close(account_id, body.expected_version)


@router.post("/{account_id}/reopen")
def reopen_account(account_id: str, body: VersionOnly, services: Svc) -> Account:
    return services.accounts.reopen(account_id, body.expected_version)
