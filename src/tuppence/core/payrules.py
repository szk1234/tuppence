"""Pay-date rules (spec §5.4). Pure functions; no database."""

from __future__ import annotations

import calendar as pycal
from collections.abc import Callable
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from tuppence.core import calendar as cal
from tuppence.core.errors import InputError


class Interval(BaseModel):
    type: Literal["weekly", "fortnightly", "four_weekly"]
    anchor: date


class MonthlyDay(BaseModel):
    type: Literal["monthly_day"]
    day: int = Field(ge=1, le=31)
    adjust: Literal["previous_working_day", "next_working_day", "none"] = "previous_working_day"


class LastWorkingDay(BaseModel):
    type: Literal["last_working_day"]


class LastWeekday(BaseModel):
    type: Literal["last_weekday"]
    weekday: int = Field(ge=0, le=6)


PayRule = Annotated[
    Interval | MonthlyDay | LastWorkingDay | LastWeekday, Field(discriminator="type")
]
_ADAPTER: TypeAdapter[PayRule] = TypeAdapter(PayRule)
_STEP = {"weekly": 7, "fortnightly": 14, "four_weekly": 28}
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# The household's nation, or a function giving it as of a date (a dated move changes which
# bank holidays apply from then on). None means England and Wales are assumed.
NationOn = str | None | Callable[[date], str | None]


def _nation_at(nation: NationOn, day: date) -> str | None:
    return nation(day) if callable(nation) else nation


def parse_rule(data: dict[str, Any]) -> PayRule:
    try:
        return _ADAPTER.validate_python(data)
    except ValidationError:
        raise InputError(
            "That pay schedule isn't valid. Choose how often you're paid and when."
        ) from None


def _month_date(rule: PayRule, year: int, month: int, nation_on: NationOn) -> date:
    """The pay date in one month; bank holidays are those of the nation on the nominal date."""
    last = date(year, month, pycal.monthrange(year, month)[1])
    if isinstance(rule, LastWorkingDay):
        return cal.last_working_day(year, month, _nation_at(nation_on, last))
    if isinstance(rule, LastWeekday):
        return last - timedelta(days=(last.weekday() - rule.weekday) % 7)
    assert isinstance(rule, MonthlyDay)
    day = date(year, month, min(rule.day, last.day))
    nation = _nation_at(nation_on, day)
    if rule.adjust == "previous_working_day":
        return cal.previous_working_day(day, nation)
    if rule.adjust == "next_working_day":
        return cal.next_working_day(day, nation)
    return day


def pay_dates(rule: PayRule, start: date, end: date, nation: NationOn) -> list[date]:
    out: list[date] = []
    if isinstance(rule, Interval):
        step = timedelta(days=_STEP[rule.type])
        offset = (start - rule.anchor).days % step.days
        d = start + timedelta(days=(step.days - offset) % step.days)
        while d <= end:
            out.append(d)
            d += step
        return out
    # Adjustments can move a date into a neighbouring month (day 31 -> next working day
    # rolls forward; day 1 -> previous working day rolls back), so generate from one month
    # before start to one month after end and filter on the adjusted date.
    y, m = (start.year - 1, 12) if start.month == 1 else (start.year, start.month - 1)
    while date(y, m, 1) <= end + timedelta(days=31):
        d = _month_date(rule, y, m, nation)
        if start <= d <= end:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(set(out))


def next_pay_date(rule: PayRule, after: date, nation: NationOn) -> date:
    found = pay_dates(rule, after + timedelta(days=1), after + timedelta(days=70), nation)
    return found[0]


def describe(rule: PayRule) -> str:
    if isinstance(rule, Interval):
        return {
            "weekly": "Every week",
            "fortnightly": "Every 2 weeks",
            "four_weekly": "Every 4 weeks",
        }[rule.type]
    if isinstance(rule, LastWorkingDay):
        return "Last working day of the month"
    if isinstance(rule, LastWeekday):
        return f"Last {_DAYS[rule.weekday]} of the month"
    suffix = (
        "th" if 11 <= rule.day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(rule.day % 10, "th")
    )
    adjust = {
        "previous_working_day": " (earlier if it's a weekend or bank holiday)",
        "next_working_day": " (later if it's a weekend or bank holiday)",
        "none": "",
    }[rule.adjust]
    return f"On the {rule.day}{suffix}{adjust}"
