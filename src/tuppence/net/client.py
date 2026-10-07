"""The only way Tuppence talks to the outside world (spec §4.6)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from tuppence.core.clock import to_iso, utcnow
from tuppence.net.hosts import is_local_host
from tuppence.net.privacy_log import PrivacyEvent, PrivacyLog, Purpose

GUARDED_PURPOSES = {"llm", "research"}


class LocalOnlyBlocked(Exception):
    def __init__(self, host: str) -> None:
        super().__init__(f"Local only is on, so Tuppence didn't contact {host}.")
        self.host = host


@dataclass
class CallContext:
    purpose: Purpose
    task: str | None = None
    connection_id: str | None = None
    local: bool = False
    redactions: int = 0


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
            "destination": request.url.host,
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
        host = request.url.host
        if (
            self.ctx.purpose in GUARDED_PURPOSES
            and self.local_only()
            and not (self.ctx.local and is_local_host(host))
        ):
            self.log.record(self._event(request, outcome="blocked", note="Local only is on"))
            raise LocalOnlyBlocked(host)
        body = request.read()
        try:
            response = self.inner.handle_request(request)
            response.read()
        except Exception as exc:
            note = f"{type(exc).__name__}: {exc}"[:300]
            self.log.record(self._event(request, bytes_out=len(body), outcome="error", note=note))
            raise
        self.log.record(
            self._event(
                request,
                bytes_out=len(body),
                bytes_in=len(response.content),
                status=response.status_code,
            )
        )
        return response

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
        trust_env=not ctx.local,
    )
