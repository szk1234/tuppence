from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.config.service import AgentView

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/config", tags=["config"])


class OverrideIn(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class ResetIn(BaseModel):
    path: str
    expected_version: int


@router.get("/agents")
def list_agents(services: Svc) -> dict[str, Any]:
    preset = services.settings.entry("config.preset")
    return {
        "agents": services.config.views(),
        "preset": {"value": preset.value, "version": preset.version},
    }


@router.get("/agents/{name}")
def get_agent(name: str, services: Svc) -> AgentView:
    return services.config.view(name)


@router.patch("/agents/{name}")
def override_agent(name: str, body: OverrideIn, services: Svc) -> AgentView:
    return services.config.set_override(name, body.changes, body.expected_version)


@router.post("/agents/{name}/reset")
def reset_agent_field(name: str, body: ResetIn, services: Svc) -> AgentView:
    return services.config.reset_field(name, body.path, body.expected_version)


@router.get("/presets")
def presets(services: Svc) -> dict[str, list[str]]:
    return {"presets": services.config.presets()}


@router.get("/export")
def export(services: Svc) -> JSONResponse:
    return JSONResponse(
        services.config.export(),
        headers={"Content-Disposition": 'attachment; filename="tuppence-agents.json"'},
    )
