from datetime import date, timedelta

from tuppence.core.periods import PeriodRules, month_of, pay_cycle_of


def paydays(start: date, end: date) -> list[date]:
    """Paid on the 25th, or the Friday before when it falls at a weekend."""
    out, month = [], date(start.year, start.month, 1)
    while month <= end:
        day = month.replace(day=25)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        if start <= day <= end:
            out.append(day)
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return out


def test_calendar_months():
    feb = month_of(date(2028, 2, 10))
    assert (feb.start, feb.end, feb.label, feb.mode) == (
        date(2028, 2, 1),
        date(2028, 2, 29),
        "February 2028",
        "calendar_month",
    )


def test_payday_to_payday():
    cycle = pay_cycle_of(date(2026, 11, 3), paydays(date(2026, 9, 1), date(2026, 12, 31)))
    assert cycle is not None
    assert (cycle.start, cycle.end) == (date(2026, 10, 23), date(2026, 11, 24))
    assert cycle.label == "23 Oct – 24 Nov 2026" and cycle.contains(date(2026, 11, 24))
    assert pay_cycle_of(date(2026, 11, 3), []) is None


def test_rules_shift_and_fall_back_to_months():
    rules = PeriodRules("pay_cycle", paydays)
    now = rules.period_for(date(2026, 11, 3))
    assert rules.shift(now, 1).start == date(2026, 11, 25)
    assert rules.shift(now, -1).start == date(2026, 9, 25)
    assert PeriodRules("pay_cycle", None).period_for(date(2026, 11, 3)).mode == "calendar_month"
    assert PeriodRules("calendar_month").shift(month_of(date(2026, 1, 15)), -1).label == (
        "December 2025"
    )
