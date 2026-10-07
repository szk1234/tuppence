from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from tuppence.app.deps import (
    AI_ADMIN_MESSAGE,
    CONFIG_ADMIN_MESSAGE,
    get_services,
    require_admin,
)
from tuppence.app.services import Services
from tuppence.core.settings_store import SettingEntry

Svc = Annotated[Services, Depends(get_services)]

HOUSEHOLD_ADMIN_MESSAGE = "Only the household admin can change household settings."


def _admin_message(key: str) -> str:
    if key.startswith(("llm.", "privacy.")):
        return AI_ADMIN_MESSAGE
    if key.startswith("config."):
        return CONFIG_ADMIN_MESSAGE
    return HOUSEHOLD_ADMIN_MESSAGE


router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingUpdate(BaseModel):
    value: Any
    expected_version: int


@router.get("")
def list_settings(services: Svc) -> dict[str, list[SettingEntry]]:
    return {"settings": services.settings.all()}


@router.patch("/{key}")
def update_setting(key: str, body: SettingUpdate, services: Svc, request: Request) -> SettingEntry:
    # Deny by default: every setting is household-wide, so in server mode only an admin may write
    # any of them (a new setting needs no extra registration). Only the 403 wording varies.
    require_admin(request, _admin_message(key))
    try:
        return services.settings.set(key, body.value, expected_version=body.expected_version)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown setting") from None
