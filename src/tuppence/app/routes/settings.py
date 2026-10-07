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

# Household-wide choices: in server mode only an admin may change them (each with its own 403 copy).
ADMIN_PREFIXES: dict[str, str] = {
    "llm.": AI_ADMIN_MESSAGE,
    "privacy.": AI_ADMIN_MESSAGE,
    "config.": CONFIG_ADMIN_MESSAGE,  # agent config is shared, like /api/config/agents
}

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingUpdate(BaseModel):
    value: Any
    expected_version: int


@router.get("")
def list_settings(services: Svc) -> dict[str, list[SettingEntry]]:
    return {"settings": services.settings.all()}


@router.patch("/{key}")
def update_setting(key: str, body: SettingUpdate, services: Svc, request: Request) -> SettingEntry:
    for prefix, message in ADMIN_PREFIXES.items():
        if key.startswith(prefix):
            require_admin(request, message)
    try:
        return services.settings.set(key, body.value, expected_version=body.expected_version)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown setting") from None
