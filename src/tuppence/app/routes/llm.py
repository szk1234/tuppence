from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from tuppence.app.deps import get_services, require_admin
from tuppence.app.services import Services
from tuppence.llm.connections import Connection, DetectedServer, ModelInfo, detect_local
from tuppence.llm.presets import PRESETS
from tuppence.llm.routing import RoutingView
from tuppence.llm.types import (
    LLMBadResponse,
    LLMConnectionError,
    LLMError,
    LLMHTTPError,
    LLMTimeout,
    Message,
)
from tuppence.net.client import LocalOnlyBlocked

Svc = Annotated[Services, Depends(get_services)]
Admin = [Depends(require_admin)]
Version = Annotated[int, Field(ge=0, le=2**31 - 1)]
TaskName = Literal["read", "categorise", "review", "research", "coach", "report", "vision"]

router = APIRouter(prefix="/api/llm", tags=["llm"])


class ConnectionIn(BaseModel):
    preset: str = Field(max_length=64)
    name: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=4000)
    headers: dict[str, str] | None = None


class ConnectionUpdate(BaseModel):
    changes: dict[str, Any]
    expected_version: Version


class ContextIn(BaseModel):
    context_window: Annotated[int, Field(ge=256, le=10_000_000)]
    expected_version: Version


class ChainRef(BaseModel):
    connection_id: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=500)


class TaskRouteIn(BaseModel):
    chain: list[ChainRef] = Field(max_length=20)
    local_only: bool = False
    expected_version: Version


class TryIn(BaseModel):
    task: TaskName = "coach"
    prompt: str = Field(min_length=1, max_length=20_000)


def classify_test_error(exc: LLMError) -> tuple[str, int | None, str]:
    """A short, fixed description of a failed connection test. Never includes response text."""
    if isinstance(exc, LLMHTTPError):
        status = exc.status
        if status in (401, 403):
            return "unauthorised", status, f"The server refused the key (HTTP {status})."
        if status == 404:
            return "not_found", status, "The server has no model list at this address (HTTP 404)."
        if status == 429:
            return "rate_limited", status, "The server is rate limiting requests (HTTP 429)."
        if status >= 500:
            return "server_error", status, f"The server had a problem (HTTP {status})."
        return "not_an_ai_server", status, f"The server answered with HTTP {status}."
    if isinstance(exc, LLMConnectionError | LLMTimeout):
        return "unreachable", None, "Couldn't connect to the server."
    if isinstance(exc, LLMBadResponse):
        return "not_an_ai_server", None, "That doesn't look like an AI server."
    return "not_an_ai_server", None, "The server's reply wasn't what an AI server sends."


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


@router.get("/detect", dependencies=Admin)
def detect(services: Svc) -> dict[str, list[DetectedServer]]:
    return {"servers": detect_local(services.client_factory)}


@router.get("/connections")
def list_connections(services: Svc) -> dict[str, list[Connection]]:
    return {"connections": services.connections.list()}


@router.post("/connections", status_code=201, dependencies=Admin)
def create_connection(body: ConnectionIn, services: Svc) -> Connection:
    return services.connections.create(
        body.preset,
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        headers=body.headers,
    )


@router.patch("/connections/{connection_id}", dependencies=Admin)
def update_connection(connection_id: str, body: ConnectionUpdate, services: Svc) -> Connection:
    return services.connections.update(connection_id, body.changes, body.expected_version)


@router.delete("/connections/{connection_id}", status_code=204, dependencies=Admin)
def delete_connection(connection_id: str, services: Svc) -> Response:
    services.connections.delete(connection_id)
    return Response(status_code=204)


@router.post("/connections/{connection_id}/acknowledge-notice", dependencies=Admin)
def acknowledge(connection_id: str, services: Svc) -> Connection:
    return services.connections.acknowledge_notice(connection_id)


@router.post("/connections/{connection_id}/test", dependencies=Admin)
def test_connection(connection_id: str, services: Svc) -> dict[str, Any]:
    """Provider problems come back as `ok: false`; the connection (with its current
    version, locality may have been rechecked) is always included."""
    services.connections.get(connection_id)  # 404 for an unknown id
    out: dict[str, Any]
    try:
        out = {"ok": True, "reason": "ok", "status": 200}
        out["models"] = services.connections.test(connection_id)
    except LocalOnlyBlocked as exc:
        out = {"ok": False, "reason": "blocked", "status": None, "error": str(exc)}
    except LLMError as exc:
        reason, status, message = classify_test_error(exc)
        out = {"ok": False, "reason": reason, "status": status, "error": message}
    out["connection"] = services.connections.get(connection_id)
    return out


@router.get("/models")
def list_models(services: Svc, connection_id: str | None = None) -> dict[str, list[ModelInfo]]:
    return {"models": services.connections.models(connection_id)}


@router.patch("/models/{connection_id}/{model_id:path}", dependencies=Admin)
def set_context(connection_id: str, model_id: str, body: ContextIn, services: Svc) -> ModelInfo:
    return services.connections.set_context_window(
        connection_id, model_id, body.context_window, body.expected_version
    )


@router.get("/routing")
def routing(services: Svc) -> RoutingView:
    return services.router.view()


@router.put("/routing/tasks/{task}", dependencies=Admin)
def set_route(task: str, body: TaskRouteIn, services: Svc) -> RoutingView:
    chain = [ref.model_dump() for ref in body.chain]
    return services.router.set_task(task, chain, body.local_only, body.expected_version)


@router.post("/try", dependencies=Admin)
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


@router.post("/secrets/forget", status_code=204, dependencies=Admin)
def forget_keys(services: Svc) -> Response:
    """Forget every saved AI key and header value; connections stay with has_key false."""
    services.connections.forget_keys()
    return Response(status_code=204)
