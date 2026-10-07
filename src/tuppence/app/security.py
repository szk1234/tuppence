"""Security headers on every response (spec §14.2)."""

from __future__ import annotations

import json
import logging
import os

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tuppence.settings import RuntimeSettings

log = logging.getLogger("tuppence")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)

_STATIC_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]


def _split_host(value: str) -> tuple[str, str | None]:
    """Split a Host header into (lower-cased hostname, port or None)."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return value, None
        host, rest = value[: end + 1], value[end + 1 :]
        return host, rest[1:] if rest.startswith(":") else None
    host, sep, port = value.partition(":")
    return host, (port if sep else None)


_LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}


def _json_response(status: int, body: dict[str, str]) -> tuple[Message, Message]:
    payload = json.dumps(body).encode()
    start: Message = {
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
    }
    return start, {"type": "http.response.body", "body": payload}


class HostAllowlistMiddleware:
    """Reject unexpected Host headers (DNS-rebinding defence).

    local/desktop: only loopback names on this server's own port.
    server: anything, unless an allow-list is configured (settings or TUPPENCE_ALLOWED_HOSTS).
    An explicit list matches hostnames only; the port is ignored. In server mode loopback names
    stay allowed alongside a list (the Docker health check probes 127.0.0.1); a rebinding page
    always sends its own hostname, never a loopback one.
    """

    def __init__(self, app: ASGIApp, settings: RuntimeSettings) -> None:
        self.app = app
        self.settings = settings
        listed = settings.allowed_hosts
        if listed is None and settings.mode == "server":
            env = os.environ.get("TUPPENCE_ALLOWED_HOSTS", "")
            listed = [h for h in (x.strip() for x in env.split(",")) if h] or None
        self.listed = {_split_host(h)[0] for h in listed} if listed else None

    def _allowed(self, scope: Scope) -> bool:
        raw = dict(scope.get("headers", [])).get(b"host", b"").decode("latin-1")
        host, port = _split_host(raw)
        if self.listed is not None and host in self.listed:
            return True
        if self.settings.mode == "server":
            return self.listed is None or host in _LOOPBACK
        if host not in _LOOPBACK:
            return False
        want = self.settings.port
        if want == 0:
            server = scope.get("server")
            want = int(server[1]) if server and server[1] else 0
        return port == str(want) or (port is None and want == 80)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or self._allowed(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        start, body = _json_response(403, {"detail": "Unexpected host."})
        await send(start)
        await send(body)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        no_store = path == "/health" or path.startswith("/api/")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if not (no_store and k.lower() == b"cache-control")
                ]
                headers.extend(_STATIC_HEADERS)
                if no_store:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send_wrapper(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if started:
                raise
            log.exception("Unhandled error handling %s %s", scope.get("method"), path)
            start, body = _json_response(500, {"detail": "Something went wrong."})
            await send_wrapper(start)
            await send_wrapper(body)
