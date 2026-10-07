"""HTTP helpers shared by adapters: one error vocabulary for all providers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from tuppence.llm.types import LLMBadResponse, LLMConnectionError, LLMHTTPError, LLMTimeout


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


_SECRET = re.compile(
    r"(bearer\s+|x-api-key[\"':\s]+|api[_-]?key[\"':=\s]+)[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE
)


def _safe_body(text: str) -> str:
    """Truncate an error body and scrub anything that looks like an echoed credential."""
    return _SECRET.sub(r"\1[redacted]", text[:500])


def _send(client: httpx.Client, method: str, url: str, **kw: Any) -> dict[str, Any]:
    try:
        response = client.request(method, url, **kw)
    except httpx.TimeoutException as exc:
        raise LLMTimeout(f"Timed out talking to {httpx.URL(url).host}") from exc
    except httpx.TransportError as exc:
        raise LLMConnectionError(f"Couldn't reach {httpx.URL(url).host}: {exc}") from exc
    if response.status_code >= 400:
        raise LLMHTTPError(
            response.status_code,
            _safe_body(response.text),
            parse_retry_after(response.headers.get("retry-after")),
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMBadResponse(f"{httpx.URL(url).host} returned something that isn't JSON") from exc
    if not isinstance(data, dict):
        raise LLMBadResponse("Unexpected response shape")
    return data


def post_json(
    client: httpx.Client, url: str, *, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    return _send(client, "POST", url, headers=headers, json=body)


def get_json(
    client: httpx.Client, url: str, *, headers: dict[str, str], params: dict[str, Any] | None = None
) -> dict[str, Any]:
    return _send(client, "GET", url, headers=headers, params=params)
