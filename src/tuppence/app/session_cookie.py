"""The session cookie: one definition of its attributes, plus sliding-expiry refresh."""

from __future__ import annotations

from typing import Any

from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SESSION_COOKIE = "tuppence_session"
MAX_AGE = 30 * 24 * 3600
RENEW_KEY = "renew_session_token"


def _cookie_kwargs(token: str, secure: bool) -> dict[str, Any]:
    return {
        "key": SESSION_COOKIE,
        "value": token,
        "max_age": MAX_AGE,
        "httponly": True,
        "samesite": "strict",
        "secure": secure,
        "path": "/",
    }


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(**_cookie_kwargs(token, secure))


def session_cookie_header(token: str, *, secure: bool) -> bytes:
    probe = Response()
    set_session_cookie(probe, token, secure=secure)
    return probe.headers["set-cookie"].encode("latin-1")


class SessionCookieRefreshMiddleware:
    """Re-issue the cookie on any response when a request renewed its session.

    Whoever calls Sessions.get and sees renewed=True sets request.state.renew_session_token,
    so the browser's expiry slides with the database's, whatever the status code.
    """

    def __init__(self, app: ASGIApp, *, secure: bool) -> None:
        self.app = app
        self.secure = secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})

        async def send_wrapper(message: Message) -> None:
            token = state.get(RENEW_KEY)
            if message["type"] == "http.response.start" and token:
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", session_cookie_header(token, secure=self.secure)))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
