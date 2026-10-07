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
from tuppence.app.session_cookie import RENEW_KEY, clear_session_cookie
from tuppence.core.auth import AuthBusy, SetupComplete, WeakPassword

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


def _info(services: Services, token: str | None, request: Request | None = None) -> SessionInfo:
    mode = services.runtime.mode
    needs_setup = mode == "server" and services.users.count() == 0
    session = services.sessions.get(token)
    if session is None or (session.kind == "launch" and mode == "server"):
        return SessionInfo(
            authenticated=False, mode=mode, needs_setup=needs_setup, user=None, csrf_token=None
        )
    if session.renewed and request is not None:
        setattr(request.state, RENEW_KEY, token)
    user = services.users.get(session.user_id) if session.user_id is not None else None
    return SessionInfo(
        authenticated=True,
        mode=mode,
        needs_setup=needs_setup,
        user=SessionUser(username=user.username, is_admin=user.is_admin) if user else None,
        csrf_token=session.csrf_token,
    )


def _busy() -> JSONResponse:
    return JSONResponse(
        {"detail": "Tuppence is busy. Try again in a moment."},
        status_code=503,
        headers={"Retry-After": "5"},
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
    set_session_cookie(response, raw, secure=services.runtime.secure_cookies)
    return response


@router.get("/api/auth/session")
def session_info(request: Request, services: Svc) -> SessionInfo:
    return _info(services, request.cookies.get(SESSION_COOKIE), request)


def _start_user_session(services: Services, user_id: int) -> JSONResponse:
    raw, _ = services.sessions.create("user", user_id)
    response = JSONResponse(_info(services, raw).model_dump())
    set_session_cookie(response, raw, secure=services.runtime.secure_cookies)
    return response


@router.post("/api/auth/setup", dependencies=[Depends(check_same_origin)])
def setup(body: Credentials, services: Svc) -> JSONResponse:
    if services.runtime.mode != "server":
        raise HTTPException(status_code=404)
    if services.users.count() > 0:  # cheap early exit: never hash for a finished setup
        raise HTTPException(status_code=409, detail="Setup is already complete.")
    try:
        user = services.users.create_first_admin(body.username, body.password)
    except SetupComplete:
        raise HTTPException(status_code=409, detail="Setup is already complete.") from None
    except WeakPassword as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except AuthBusy:
        return _busy()
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
    try:
        user = services.users.authenticate(body.username, body.password)
    except AuthBusy:
        services.limiter.refund_attempt(key)  # a busy server isn't the user's failed guess
        return _busy()
    if user is None:
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    services.limiter.reset(key)
    return _start_user_session(services, user.id)


@router.post("/api/auth/logout", status_code=204, dependencies=[Depends(require_session)])
def logout(request: Request, services: Svc) -> Response:
    services.sessions.delete(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    clear_session_cookie(response)
    setattr(request.state, RENEW_KEY, None)  # don't re-issue the token we just deleted
    return response
