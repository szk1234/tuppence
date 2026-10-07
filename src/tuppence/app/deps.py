from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel

from tuppence.app.services import Services
from tuppence.app.session_cookie import RENEW_KEY, SESSION_COOKIE, set_session_cookie

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
    token = request.cookies.get(SESSION_COOKIE)
    session = services.sessions.get(token)
    if session is None or (session.kind == "launch" and services.runtime.mode == "server"):
        raise HTTPException(status_code=401, detail="Sign in required.")
    if session.renewed:
        setattr(request.state, RENEW_KEY, token)  # middleware slides the browser's expiry too
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


AI_ADMIN_MESSAGE = "Only the household admin can change AI connections."
CONFIG_ADMIN_MESSAGE = "Only the household admin can change agent settings."


def require_admin(request: Request, message: str = AI_ADMIN_MESSAGE) -> None:
    """Server mode: only an admin account. Local and desktop: the one user is the admin."""
    services = get_services(request)
    if services.runtime.mode != "server":
        return
    principal: Principal | None = getattr(request.state, "principal", None)
    user = (
        services.users.get(principal.user_id)
        if principal is not None and principal.user_id is not None
        else None
    )
    if user is None or not user.is_admin:
        raise HTTPException(status_code=403, detail=message)


def admin_only(message: str) -> Callable[[Request], None]:
    """A dependency that needs an admin, with its own 403 wording."""

    def check(request: Request) -> None:
        require_admin(request, message)

    return check


def check_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("host", "")
    if origin.split("://", 1)[-1].rstrip("/") != host:
        raise HTTPException(status_code=403, detail="Cross-site request blocked.")


__all__ = [
    "AI_ADMIN_MESSAGE",
    "CONFIG_ADMIN_MESSAGE",
    "SESSION_COOKIE",
    "Principal",
    "admin_only",
    "check_same_origin",
    "get_services",
    "require_admin",
    "require_session",
    "set_session_cookie",
]
