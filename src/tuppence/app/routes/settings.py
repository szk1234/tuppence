from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.core.settings_store import SettingEntry

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingUpdate(BaseModel):
    value: Any
    expected_version: int


@router.get("")
def list_settings(services: Svc) -> dict[str, list[SettingEntry]]:
    return {"settings": services.settings.all()}


@router.patch("/{key}")
def update_setting(key: str, body: SettingUpdate, services: Svc) -> SettingEntry:
    try:
        return services.settings.set(key, body.value, expected_version=body.expected_version)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown setting") from None
