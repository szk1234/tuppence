from __future__ import annotations

import secrets
from typing import Literal

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

from tuppence.app.services import Services

SESSION_COOKIE = "tuppence_session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_AGE = 30 * 24 * 3600


class Principal(BaseModel):
    kind: Literal["user", "launch"]
    user_id: int | None
    username: str | None
    csrf_token: str


def get_services(request: Request) -> Services:
    return request.app.state.services


def set_session_cookie(response: Response, token: str, services: Services) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=services.runtime.secure_cookies,
        path="/",
    )


def require_session(request: Request, response: Response) -> Principal:
    services = get_services(request)
    token = request.cookies.get(SESSION_COOKIE)
    session = services.sessions.get(token)
    if session is None or (session.kind == "launch" and services.runtime.mode == "server"):
        raise HTTPException(status_code=401, detail="Sign in required.")
    if session.renewed and token:
        set_session_cookie(response, token, services)  # slide the browser's expiry too
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
