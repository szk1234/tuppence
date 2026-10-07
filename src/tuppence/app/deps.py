from __future__ import annotations

import secrets
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel

from tuppence.app.services import Services

SESSION_COOKIE = "tuppence_session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class Principal(BaseModel):
    kind: Literal["user", "launch"]
    user_id: int | None
    username: str | None
    csrf_token: str


def get_services(request: Request) -> Services:
    return request.app.state.services


def require_session(request: Request) -> Principal:
    services = get_services(request)
    session = services.sessions.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(sent.encode(), session.csrf_token.encode()):
            raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")
    username = None
    if session.user_id is not None:
        user = services.users.get(session.user_id)
        username = user.username if user else None
    principal = Principal(
        kind=session.kind,
        user_id=session.user_id,
        username=username,
        csrf_token=session.csrf_token,
    )
    request.state.principal = principal
    return principal


def check_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("host", "")
    if origin.split("://", 1)[-1].rstrip("/") != host:
        raise HTTPException(status_code=403, detail="Cross-site request blocked.")
