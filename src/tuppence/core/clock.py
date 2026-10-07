"""Time helpers. All stored timestamps are UTC ISO strings with a trailing Z."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime(_FMT)


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, _FMT).replace(tzinfo=UTC)


def months_ago(dt: datetime, months: int) -> datetime:
    """The same time `months` calendar months earlier (the day clamped to that month's end)."""
    index = dt.year * 12 + (dt.month - 1) - months
    year, month = divmod(index, 12)
    day = min(dt.day, calendar.monthrange(year, month + 1)[1])
    return dt.replace(year=year, month=month + 1, day=day)
