"""The only way Tuppence talks to the outside world (spec §4.6)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from tuppence.core.clock import to_iso, utcnow
from tuppence.net.hosts import is_local_host
from tuppence.net.privacy_log import PrivacyEvent, PrivacyLog, Purpose

GUARDED_PURPOSES = {"llm", "research"}
_log = logging.getLogger("tuppence.privacy")


class LocalOnlyBlocked(Exception):
    def __init__(self, host: str, *, pinned: bool = False) -> None:
        if pinned:
            msg = f"This task is set to use only local models, so Tuppence didn't contact {host}."
        else:
            msg = f"Local only is on, so Tuppence didn't contact {host}."
        super().__init__(msg)
        self.host = host
        self.pinned = pinned


@dataclass
class CallContext:
    purpose: Purpose
    task: str | None = None
    connection_id: str | None = None
    local: bool = False
    redactions: int = 0
    # Block any non-local host for this call whatever the global Local only setting is
    # (a task pinned to local models).
    require_local: bool = False


def _wire_host(request: httpx.Request) -> str:
    """The IDNA/ASCII host httpcore connects to, not httpx's decoded Unicode form."""
    return request.url.raw_host.decode("ascii")


class GuardedTransport(httpx.BaseTransport):
    def __init__(
        self,
        inner: httpx.BaseTransport,
        ctx: CallContext,
        log: PrivacyLog,
        local_only: Callable[[], bool],
    ) -> None:
        self.inner, self.ctx, self.log, self.local_only = inner, ctx, log, local_only

    def _event(self, request: httpx.Request, **kw: Any) -> PrivacyEvent:
        base: dict[str, Any] = {
            "ts": to_iso(utcnow()),
            "purpose": self.ctx.purpose,
            "task": self.ctx.task,
            "connection_id": self.ctx.connection_id,
            "destination": _wire_host(request),
            "method": request.method,
            "path": request.url.path,
            "bytes_out": 0,
            "bytes_in": 0,
            "status": None,
            "redactions": self.ctx.redactions,
            "outcome": "sent",
            "note": None,
        }
        base.update(kw)
        return PrivacyEvent.model_validate(base)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = _wire_host(request)
        if (
            self.ctx.purpose in GUARDED_PURPOSES
            and (self.ctx.require_local or self.local_only())
            and not (self.ctx.local and is_local_host(host))
        ):
            pinned = self.ctx.require_local and not self.local_only()
            note = "Task is set to local models only" if pinned else "Local only is on"
            self.log.record(self._event(request, outcome="blocked", note=note))
            raise LocalOnlyBlocked(host, pinned=pinned)
        body = request.read()
        try:
            response = self.inner.handle_request(request)
            response.read()
        except Exception as exc:
            # Class name only: exception text can echo header values (API keys).
            self._record_after_send(
                self._event(request, bytes_out=len(body), outcome="error", note=type(exc).__name__)
            )
            raise
        self._record_after_send(
            self._event(
                request,
                bytes_out=len(body),
                bytes_in=len(response.content),
                status=response.status_code,
            )
        )
        return response

    def _record_after_send(self, event: PrivacyEvent) -> None:
        # The request has already left; a log failure must not turn it into a failed call.
        try:
            self.log.record(event)
        except Exception as exc:
            _log.error("privacy log write failed after send: %s", type(exc).__name__)

    def close(self) -> None:
        self.inner.close()


def make_client(
    ctx: CallContext,
    *,
    privacy_log: PrivacyLog,
    local_only: Callable[[], bool],
    timeout: float,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    inner = transport or httpx.HTTPTransport(retries=0)
    return httpx.Client(
        transport=GuardedTransport(inner, ctx, privacy_log, local_only),
        timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
        follow_redirects=False,
        # No trust_env: env proxies are intentionally unused because the guard owns the transport.
        # Proxy support, if ever added, belongs on the inner HTTPTransport.
    )
