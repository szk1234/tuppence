"""Reporting periods (spec §5.4): the calendar month by default, or a pay cycle anchored on
one adult's main income ("payday to payday")."""

from __future__ import annotations

import calendar
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

PeriodMode = Literal["calendar_month", "pay_cycle"]


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    mode: PeriodMode
    label: str

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def _uk(day: date, *, year: bool = True) -> str:
    return f"{day.day} {day.strftime('%b')}" + (f" {day.year}" if year else "")


def month_of(day: date) -> Period:
    last = calendar.monthrange(day.year, day.month)[1]
    return Period(
        date(day.year, day.month, 1),
        date(day.year, day.month, last),
        "calendar_month",
        day.strftime("%B %Y"),
    )


def pay_cycle_of(day: date, pay_days: Sequence[date]) -> Period | None:
    """From the last payday on or before `day` to the day before the next one."""
    days = sorted(set(pay_days))
    starts = [d for d in days if d <= day]
    ends = [d for d in days if d > day]
    if not starts or not ends:
        return None
    start, end = starts[-1], ends[0] - timedelta(days=1)
    label = f"{_uk(start, year=start.year != end.year)} – {_uk(end)}"
    return Period(start, end, "pay_cycle", label)


@dataclass
class PeriodRules:
    """How this household splits time. `pay_days(start, end)` lists the anchor income's
    paydays in a range, or is None when the household uses calendar months (or has no
    pay rule to anchor on)."""

    mode: PeriodMode
    pay_days: Callable[[date, date], list[date]] | None = None

    def period_for(self, day: date) -> Period:
        if self.mode == "pay_cycle" and self.pay_days is not None:
            found = pay_cycle_of(
                day, self.pay_days(day - timedelta(days=70), day + timedelta(days=70))
            )
            if found is not None:
                return found
        return month_of(day)

    def shift(self, period: Period, steps: int) -> Period:
        current = period
        for _ in range(abs(steps)):
            current = self.period_for(
                current.end + timedelta(days=1) if steps > 0 else current.start - timedelta(days=1)
            )
        return current
