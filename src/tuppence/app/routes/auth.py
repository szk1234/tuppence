"""Sign-in: launch links (local/desktop) and accounts (server mode)."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from tuppence.app.deps import (
    SESSION_COOKIE,
    check_same_origin,
    get_services,
    require_session,
    set_session_cookie,
)
from tuppence.app.services import Services
from tuppence.core.auth import SetupComplete, WeakPassword

Svc = Annotated[Services, Depends(get_services)]

router = APIRouter(tags=["auth"])

EXPIRED_HTML = (
    '<!doctype html><html lang="en-GB"><meta charset="utf-8"><title>Tuppence</title>'
    '<body style="font-family: system-ui, sans-serif; max-width: 36rem; margin: 3rem auto;'
    ' padding: 0 1rem"><h1>This link has expired</h1>'
    "<p>Close this tab and open Tuppence again from your apps or terminal.</p></body></html>"
)


class Credentials(BaseModel):
    username: str
    password: str


class SessionUser(BaseModel):
    username: str
    is_admin: bool


class SessionInfo(BaseModel):
    authenticated: bool
    mode: str
    needs_setup: bool
    user: SessionUser | None
    csrf_token: str | None


def _info(services: Services, token: str | None) -> SessionInfo:
    mode = services.runtime.mode
    needs_setup = mode == "server" and services.users.count() == 0
    session = services.sessions.get(token)
    if session is None:
        return SessionInfo(
            authenticated=False, mode=mode, needs_setup=needs_setup, user=None, csrf_token=None
        )
    user = services.users.get(session.user_id) if session.user_id is not None else None
    return SessionInfo(
        authenticated=True,
        mode=mode,
        needs_setup=needs_setup,
        user=SessionUser(username=user.username, is_admin=user.is_admin) if user else None,
        csrf_token=session.csrf_token,
    )


@router.get("/auth/launch", include_in_schema=False)
def launch(token: str, services: Svc) -> Response:
    expected = services.runtime.launch_token
    if services.runtime.mode == "server" or not expected:
        raise HTTPException(status_code=404)
    if not secrets.compare_digest(token.encode(), expected.encode()):
        return HTMLResponse(EXPIRED_HTML, status_code=403)
    if not services.consume_launch_token():
        return HTMLResponse(EXPIRED_HTML, status_code=403)
    raw, _ = services.sessions.create("launch")
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, raw, services)
    return response


@router.get("/api/auth/session")
def session_info(request: Request, services: Svc) -> SessionInfo:
    return _info(services, request.cookies.get(SESSION_COOKIE))


def _start_user_session(services: Services, user_id: int) -> JSONResponse:
    raw, _ = services.sessions.create("user", user_id)
    response = JSONResponse(_info(services, raw).model_dump())
    set_session_cookie(response, raw, services)
    return response


@router.post("/api/auth/setup", dependencies=[Depends(check_same_origin)])
def setup(body: Credentials, services: Svc) -> JSONResponse:
    if services.runtime.mode != "server":
        raise HTTPException(status_code=404)
    try:
        user = services.users.create_first_admin(body.username, body.password)
    except SetupComplete:
        raise HTTPException(status_code=409, detail="Setup is already complete.") from None
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _start_user_session(services, user.id)


@router.post("/api/auth/login", dependencies=[Depends(check_same_origin)])
def login(body: Credentials, request: Request, services: Svc) -> JSONResponse:
    if services.runtime.mode != "server":
        raise HTTPException(status_code=404)
    ip = request.client.host if request.client else "unknown"
    key = f"{ip}|{body.username.strip().lower()}"
    wait = services.limiter.begin_attempt(key)  # charged up front, so bursts can't bypass it
    if wait is not None:
        return JSONResponse(
            {"detail": f"Too many attempts. Try again in {wait} seconds."},
            status_code=429,
            headers={"Retry-After": str(wait)},
        )
    user = services.users.authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    services.limiter.reset(key)
    return _start_user_session(services, user.id)


@router.post("/api/auth/logout", status_code=204, dependencies=[Depends(require_session)])
def logout(request: Request, services: Svc) -> Response:
    services.sessions.delete(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
