"""Time helpers. All stored timestamps are UTC ISO strings with a trailing Z."""

from __future__ import annotations

from datetime import UTC, datetime

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime(_FMT)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, _FMT).replace(tzinfo=UTC)
