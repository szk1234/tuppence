from datetime import date

import pytest

from tuppence.core.errors import InputError
from tuppence.core.payrules import describe, next_pay_date, parse_rule, pay_dates


def test_monthly_day_moves_back_over_christmas():
    rule = parse_rule({"type": "monthly_day", "day": 25})
    assert next_pay_date(rule, date(2026, 12, 1), "england") == date(2026, 12, 24)


def test_monthly_day_31_clamps_then_adjusts():
    rule = parse_rule({"type": "monthly_day", "day": 31})
    dates = pay_dates(rule, date(2027, 2, 1), date(2027, 4, 30), "england")
    assert dates == [
        date(2027, 2, 26),
        date(2027, 3, 31),
        date(2027, 4, 30),
    ]  # 28 Feb 2027 is a Sunday


def test_next_working_day_adjust_and_none():
    nxt = parse_rule({"type": "monthly_day", "day": 17, "adjust": "next_working_day"})
    assert next_pay_date(nxt, date(2026, 10, 1), "england") == date(
        2026, 10, 19
    )  # 17 Oct 2026 is a Saturday
    none = parse_rule({"type": "monthly_day", "day": 17, "adjust": "none"})
    assert next_pay_date(none, date(2026, 10, 1), "england") == date(2026, 10, 17)


def test_next_working_day_rolls_forward_across_month_boundary():
    rule = parse_rule({"type": "monthly_day", "day": 31, "adjust": "next_working_day"})
    # 28 Feb 2027 is a Sunday -> Mon 1 Mar 2027; must not be lost when start is 1 Mar
    assert pay_dates(rule, date(2027, 3, 1), date(2027, 3, 1), "england") == [date(2027, 3, 1)]
    assert pay_dates(rule, date(2027, 2, 2), date(2027, 3, 31), "england") == [
        date(2027, 3, 1),
        date(2027, 3, 31),
    ]
    assert next_pay_date(rule, date(2027, 2, 27), "england") == date(2027, 3, 1)
    assert next_pay_date(rule, date(2027, 3, 1), "england") == date(2027, 3, 31)


def test_bank_holiday_roll_across_month_boundary():
    # 1 Jan 2028 is a Saturday and Mon 3 Jan is the substitute bank holiday -> Tue 4 Jan
    rule = parse_rule({"type": "monthly_day", "day": 1, "adjust": "next_working_day"})
    assert pay_dates(rule, date(2028, 1, 1), date(2028, 1, 31), "england") == [date(2028, 1, 4)]
    assert next_pay_date(rule, date(2027, 12, 31), "england") == date(2028, 1, 4)


def test_day_one_previous_working_day_lands_in_prior_month():
    rule = parse_rule({"type": "monthly_day", "day": 1})
    # 1 Jan 2028 is a Saturday -> Fri 31 Dec 2027 (the January pay date lands in December)
    assert pay_dates(rule, date(2027, 12, 2), date(2027, 12, 31), "england") == [date(2027, 12, 31)]
    assert next_pay_date(rule, date(2027, 12, 15), "england") == date(2027, 12, 31)
    assert next_pay_date(rule, date(2027, 12, 31), "england") == date(2028, 2, 1)


def test_last_working_day_rule():
    rule = parse_rule({"type": "last_working_day"})
    assert pay_dates(rule, date(2027, 5, 1), date(2027, 6, 30), "england") == [
        date(2027, 5, 28),
        date(2027, 6, 30),
    ]


def test_last_weekday_rule():
    rule = parse_rule({"type": "last_weekday", "weekday": 4})
    assert next_pay_date(rule, date(2026, 10, 1), "england") == date(2026, 10, 30)


def test_interval_rules():
    rule = parse_rule({"type": "four_weekly", "anchor": "2026-09-11"})
    assert pay_dates(rule, date(2026, 9, 1), date(2026, 11, 30), "england") == [
        date(2026, 9, 11),
        date(2026, 10, 9),
        date(2026, 11, 6),
    ]
    weekly = parse_rule({"type": "weekly", "anchor": "2026-10-02"})
    assert next_pay_date(weekly, date(2026, 10, 2), "england") == date(2026, 10, 9)
    fortnightly = parse_rule({"type": "fortnightly", "anchor": "2026-10-02"})
    assert next_pay_date(fortnightly, date(2026, 9, 1), "england") == date(
        2026, 9, 4
    )  # works backwards from anchor


def test_strictly_after():
    rule = parse_rule({"type": "monthly_day", "day": 15, "adjust": "none"})
    assert next_pay_date(rule, date(2026, 10, 15), "england") == date(2026, 11, 15)


def test_describe():
    assert describe(parse_rule({"type": "four_weekly", "anchor": "2026-09-11"})) == "Every 4 weeks"
    assert (
        describe(parse_rule({"type": "monthly_day", "day": 17}))
        == "On the 17th (earlier if it's a weekend or bank holiday)"
    )
    assert (
        describe(parse_rule({"type": "last_weekday", "weekday": 4})) == "Last Friday of the month"
    )


@pytest.mark.parametrize(
    "bad", [{"type": "monthly_day", "day": 32}, {"type": "sometimes"}, {"type": "weekly"}]
)
def test_bad_rules(bad):
    with pytest.raises(InputError):
        parse_rule(bad)


def test_nation_can_change_by_date():
    """A move to Scotland from 1 Nov 2026: St Andrew's Day (Mon 30 Nov) is then a bank holiday."""
    rule = parse_rule({"type": "monthly_day", "day": 30, "adjust": "previous_working_day"})

    def nation_on(day: date) -> str | None:
        return "scotland" if day >= date(2026, 11, 1) else "england"

    assert pay_dates(rule, date(2026, 10, 1), date(2026, 11, 30), nation_on) == [
        date(2026, 10, 30),
        date(2026, 11, 27),
    ]
    assert pay_dates(rule, date(2026, 11, 1), date(2026, 11, 30), "england") == [date(2026, 11, 30)]
    assert next_pay_date(rule, date(2026, 11, 1), nation_on) == date(2026, 11, 27)
