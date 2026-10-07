"""UK bank holidays per nation (gov.uk data bundled; refreshed by data packs in M6)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import cache
from importlib import resources


def division_for(nation: str | None) -> str:
    if nation == "scotland":
        return "scotland"
    if nation == "northern_ireland":
        return "northern-ireland"
    return "england-and-wales"


@cache
def _data() -> dict[str, frozenset[date]]:
    raw = json.loads(
        resources.files("tuppence.datapacks.baseline")
        .joinpath("uk-bank-holidays.json")
        .read_text(encoding="utf-8")
    )
    return {k: frozenset(date.fromisoformat(d) for d in v) for k, v in raw["divisions"].items()}


def bank_holidays(nation: str | None) -> frozenset[date]:
    return _data()[division_for(nation)]


def coverage_end() -> date:
    """Last date in the bundled data; beyond it only weekends count as non-working."""
    return max(max(v) for v in _data().values())


def is_working_day(day: date, nation: str | None) -> bool:
    return day.weekday() < 5 and day not in bank_holidays(nation)


def previous_working_day(day: date, nation: str | None) -> date:
    while not is_working_day(day, nation):
        day -= timedelta(days=1)
    return day


def next_working_day(day: date, nation: str | None) -> date:
    while not is_working_day(day, nation):
        day += timedelta(days=1)
    return day


def last_working_day(year: int, month: int, nation: str | None) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    return previous_working_day(first_next - timedelta(days=1), nation)
