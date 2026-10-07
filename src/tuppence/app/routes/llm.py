from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from tuppence.app.deps import get_services
from tuppence.app.services import Services
from tuppence.llm.connections import Connection, DetectedServer, ModelInfo, detect_local
from tuppence.llm.presets import PRESETS
from tuppence.llm.routing import RoutingView
from tuppence.llm.types import LLMError, Message
from tuppence.net.client import LocalOnlyBlocked

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(prefix="/api/llm", tags=["llm"])


class ConnectionIn(BaseModel):
    preset: str
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None


class ConnectionUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: int


class ContextIn(BaseModel):
    context_window: int
    expected_version: int


class TaskRouteIn(BaseModel):
    chain: list[dict[str, str]]
    local_only: bool = False
    expected_version: int


class TryIn(BaseModel):
    task: str = "coach"
    prompt: str


@router.get("/presets")
def presets() -> dict[str, list[dict[str, Any]]]:
    return {
        "presets": [
            {
                "id": p.id,
                "label": p.label,
                "api_style": p.api_style,
                "base_url": p.base_url,
                "kind": p.kind,
                "key_required": p.key_required,
            }
            for p in PRESETS.values()
        ]
    }


@router.get("/detect")
def detect(services: Svc) -> dict[str, list[DetectedServer]]:
    return {"servers": detect_local(services.client_factory)}


@router.get("/connections")
def list_connections(services: Svc) -> dict[str, list[Connection]]:
    return {"connections": services.connections.list()}


@router.post("/connections", status_code=201)
def create_connection(body: ConnectionIn, services: Svc) -> Connection:
    return services.connections.create(
        body.preset,
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        headers=body.headers,
    )


@router.patch("/connections/{connection_id}")
def update_connection(connection_id: str, body: ConnectionUpdate, services: Svc) -> Connection:
    return services.connections.update(connection_id, body.changes, body.expected_version)


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(connection_id: str, services: Svc) -> Response:
    services.connections.delete(connection_id)
    return Response(status_code=204)


@router.post("/connections/{connection_id}/acknowledge-notice")
def acknowledge(connection_id: str, services: Svc) -> Connection:
    return services.connections.acknowledge_notice(connection_id)


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: str, services: Svc) -> dict[str, Any]:
    """Provider problems come back as `ok: false`; the connection (with its current
    version, locality may have been rechecked) is always included."""
    services.connections.get(connection_id)  # 404 for an unknown id
    out: dict[str, Any]
    try:
        out = {"ok": True, "models": services.connections.test(connection_id)}
    except (LLMError, LocalOnlyBlocked) as exc:
        out = {"ok": False, "error": str(exc)}
    out["connection"] = services.connections.get(connection_id)
    return out


@router.get("/models")
def list_models(services: Svc, connection_id: str | None = None) -> dict[str, list[ModelInfo]]:
    return {"models": services.connections.models(connection_id)}


@router.patch("/models/{connection_id}/{model_id:path}")
def set_context(connection_id: str, model_id: str, body: ContextIn, services: Svc) -> ModelInfo:
    return services.connections.set_context_window(
        connection_id, model_id, body.context_window, body.expected_version
    )


@router.get("/routing")
def routing(services: Svc) -> RoutingView:
    return services.router.view()


@router.put("/routing/tasks/{task}")
def set_route(task: str, body: TaskRouteIn, services: Svc) -> RoutingView:
    return services.router.set_task(task, body.chain, body.local_only, body.expected_version)


@router.post("/try")
def try_model(body: TryIn, services: Svc) -> dict[str, Any]:
    result = services.llm.chat(
        body.task, [Message(role="user", content=body.prompt)], max_tokens=512
    )
    return {
        "text": result.text,
        "connection_id": result.connection_id,
        "model_id": result.model_id,
        "redactions": result.redactions,
        "usage": result.usage.model_dump(),
        "cost_gbp": result.cost_gbp,
    }


@router.post("/secrets/forget", status_code=204)
def forget_keys(services: Svc) -> Response:
    """Forget every saved AI key and header value; connections stay with has_key false."""
    services.connections.forget_keys()
    return Response(status_code=204)
