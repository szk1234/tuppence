"""Errors shared by the domain core (no web-framework imports here)."""

from __future__ import annotations


class UserFacing:
    """Mixin for exceptions whose text Tuppence writes for people.

    Such text never quotes request data (API keys, header values, bodies), so it may be
    stored and shown as it is. Every other exception is shown as `safe_error_text` says.
    """


class InputError(UserFacing, ValueError):
    """User input that's well-formed but not acceptable (HTTP 422)."""


def _http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)  # LLMHTTPError
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    try:
        response = getattr(exc, "response", None)  # httpx.HTTPStatusError
    except Exception:  # noqa: BLE001 - some libraries raise from a missing response
        return None
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def safe_error_text(exc: BaseException) -> str:
    """The only way an exception becomes text that is stored or returned.

    A message Tuppence wrote (`UserFacing`) is kept. Anything else (network, provider and
    library errors, whose text can echo request headers such as API keys) becomes its class
    name, plus the HTTP status when it has one.
    """
    if isinstance(exc, UserFacing):
        return str(exc)
    name = type(exc).__name__
    status = _http_status(exc)
    return f"{name} (HTTP {status})" if status is not None else name
