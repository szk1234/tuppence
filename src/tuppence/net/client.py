"""The only way Tuppence talks to the outside world (spec §4.6)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from tuppence.core.clock import to_iso, utcnow
from tuppence.net import hosts
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


class MetadataHostBlocked(LocalOnlyBlocked):
    """A cloud instance-metadata address: blocked for every purpose."""

    def __init__(self, host: str) -> None:
        Exception.__init__(
            self, f"Tuppence never contacts {host}: it is a cloud instance-metadata address."
        )
        self.host = host
        self.pinned = False


class GuardedTransport(httpx.BaseTransport):
    def __init__(
        self,
        inner: httpx.BaseTransport,
        ctx: CallContext,
        log: PrivacyLog,
        local_only: Callable[[], bool],
    ) -> None:
        self.inner, self.ctx, self.log, self.local_only = inner, ctx, log, local_only

    def _event(self, request: httpx.Request, host: str, **kw: Any) -> PrivacyEvent:
        base: dict[str, Any] = {
            "ts": to_iso(utcnow()),
            "purpose": self.ctx.purpose,
            "task": self.ctx.task,
            "connection_id": self.ctx.connection_id,
            "destination": host,
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

    def _vet(self, host: str) -> tuple[list[str], bool]:
        """Resolve `host` once. Returns its addresses and whether it is a literal address."""
        if hosts.parse_address(host) is not None:
            return [host.strip("[]")], True
        found = hosts._system_resolve(host)
        if not found and host == "localhost":
            found = ["127.0.0.1"]
        # IPv4 first, so the pinned choice is stable.
        return sorted(found, key=lambda a: (":" in a, a)), False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = _wire_host(request)
        addresses, literal = self._vet(host)
        if hosts.is_metadata_name(host) or any(hosts.is_metadata_address(a) for a in addresses):
            self.log.record(
                self._event(request, host, outcome="blocked", note="Cloud metadata address")
            )
            raise MetadataHostBlocked(host)
        local = bool(addresses) and all(hosts.is_local_address(a) for a in addresses)
        if (
            self.ctx.purpose in GUARDED_PURPOSES
            and (self.ctx.require_local or self.local_only())
            and not (self.ctx.local and local)
        ):
            pinned = self.ctx.require_local and not self.local_only()
            note = "Task is set to local models only" if pinned else "Local only is on"
            self.log.record(self._event(request, host, outcome="blocked", note=note))
            raise LocalOnlyBlocked(host, pinned=pinned)
        body = request.read()
        try:
            self._pin(request, host, addresses, literal)
            response = self.inner.handle_request(request)
            response.read()
        except Exception as exc:
            # Class name only: exception text can echo header values (API keys).
            self._record_after_send(
                self._event(
                    request,
                    host,
                    bytes_out=len(body),
                    outcome="error",
                    note=type(exc).__name__,
                )
            )
            raise
        self._record_after_send(
            self._event(
                request,
                host,
                bytes_out=len(body),
                bytes_in=len(response.content),
                status=response.status_code,
            )
        )
        return response

    def _pin(self, request: httpx.Request, host: str, addresses: list[str], literal: bool) -> None:
        """Connect to the address that was vetted, not to whatever the name resolves to later."""
        if literal:
            return
        if not addresses:
            if isinstance(self.inner, httpx.HTTPTransport):
                raise httpx.ConnectError(f"Couldn't resolve {host}")
            return  # a test transport never resolves names
        request.url = request.url.copy_with(host=addresses[0])  # Host header is unchanged
        if request.url.scheme == "https":
            request.extensions["sni_hostname"] = host.rstrip(".")

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
