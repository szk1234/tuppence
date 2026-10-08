from datetime import date, timedelta

import pytest

from tuppence.knowledge.cadence import (
    Payment,
    annual_cost,
    consistent_day,
    detect,
    due_dates,
    fit_cadence,
    next_due,
    status_of,
)


def pays(dates, amounts):
    if isinstance(amounts, int):
        amounts = [amounts] * len(dates)
    return [Payment(f"t{i}", d, a) for i, (d, a) in enumerate(zip(dates, amounts, strict=True))]


def monthly(day, months, year=2026, start=1):
    return [
        date(year + (start - 1 + m) // 12, (start - 1 + m) % 12 + 1, day) for m in range(months)
    ]


def every(days, n, start=date(2026, 1, 5)):
    return [start + timedelta(days=days * i) for i in range(n)]


@pytest.mark.parametrize(
    "dates,cadence",
    [
        (every(7, 6), "weekly"),
        (every(14, 5), "fortnightly"),
        (every(28, 6), "four_weekly"),
        (monthly(14, 6), "monthly"),
        (every(91, 4), "quarterly"),
        ([date(2025, 3, 2), date(2026, 3, 1)], "annual"),
    ],
)
def test_each_cadence_is_recognised_from_its_gaps(dates, cadence):
    assert fit_cadence(dates)[0] == cadence


def test_monthly_on_weekdays_and_month_ends_still_counts():
    # The 1st moved to the next working day, and "last working day" pay.
    assert (
        fit_cadence(
            [
                date(2026, 2, 2),
                date(2026, 3, 2),
                date(2026, 4, 1),
                date(2026, 5, 1),
                date(2026, 6, 1),
            ]
        )[0]
        == "monthly"
    )
    month_end = [date(2026, 1, 30), date(2026, 2, 27), date(2026, 3, 31), date(2026, 4, 30)]
    assert fit_cadence(month_end)[0] == "monthly" and consistent_day(month_end) == 0


def test_four_weekly_drifts_through_the_month_but_monthly_does_not():
    assert consistent_day(every(28, 5)) is None
    assert consistent_day(monthly(3, 5)) == 3


def test_irregular_shopping_is_not_a_cadence():
    shops = [date(2026, 1, d) for d in (2, 3, 9, 17, 18, 30)]
    assert fit_cadence(shops) is None
    assert detect(pays(shops, [4218, 6120, 3311, 9802, 2210, 5007]), as_of=date(2026, 2, 1)) == []


def test_a_missed_month_is_allowed_and_reported():
    dates = [date(2026, m, 14) for m in (1, 2, 3, 5, 6)]
    [series] = detect(pays(dates, 999), as_of=date(2026, 6, 30))
    assert series.cadence == "monthly" and series.missed == (date(2026, 4, 14),)


def test_price_rise_keeps_one_series_with_its_history():
    dates = monthly(14, 8)
    [series] = detect(pays(dates, [999] * 5 + [1199] * 3), as_of=date(2026, 8, 31))
    assert series.expected_amount_pence == 1199 and not series.varies
    assert [(s.since, s.amount_pence) for s in series.price_steps] == [
        (date(2026, 1, 14), 999),
        (date(2026, 6, 14), 1199),
    ]
    assert annual_cost(series) == 1199 * 12


def test_a_bill_that_varies_has_no_price_steps():
    dates = monthly(20, 6)
    [series] = detect(pays(dates, [3120, 3415, 2980, 3550, 3205, 3390]), as_of=date(2026, 7, 1))
    assert series.varies and series.price_steps == () and series.expected_amount_pence == 3390


def test_two_prices_from_one_merchant_are_two_series():
    a = pays(monthly(3, 6), 799)
    b = [Payment(f"b{i}", d, 1599) for i, d in enumerate(monthly(20, 6))]
    found = detect(a + b, as_of=date(2026, 7, 1))
    assert sorted(s.expected_amount_pence for s in found) == [799, 1599]


def test_a_subscription_among_one_off_purchases_from_the_same_shop():
    prime = pays(monthly(5, 5), 899)
    extras = [
        Payment("x1", date(2026, 1, 19), 850),
        Payment("x2", date(2026, 2, 26), 920),
        Payment("x3", date(2026, 4, 11), 875),
    ]
    [series] = detect(prime + extras, as_of=date(2026, 6, 1))
    assert series.expected_amount_pence == 899 and len(series.payments) == 5


def test_free_trial_then_full_price():
    dates = [date(2026, 3, 1)] + monthly(1, 4, start=4)
    [series] = detect(pays(dates, [100, 799, 799, 799, 799]), as_of=date(2026, 7, 15))
    assert series.trial is not None and series.trial.amount_pence == 100
    assert len(series.payments) == 4 and series.expected_amount_pence == 799


def test_lapsed_after_two_missed_months_and_next_due():
    [series] = detect(pays(monthly(10, 4), 2500), as_of=date(2026, 5, 1))
    assert (
        next_due(series) == date(2026, 5, 10) and status_of(series, date(2026, 5, 20)) == "active"
    )
    assert status_of(series, date(2026, 7, 20)) == "lapsed"


def test_council_tax_in_ten_instalments_is_not_lapsed_in_february():
    dates = [date(2026, m, 1) for m in range(4, 13)] + [date(2027, 1, 1)]
    [series] = detect(pays(dates, 14200), as_of=date(2027, 3, 31), expected_gaps=frozenset({2, 3}))
    assert series.skip_months == (2, 3) and series.missed == ()
    assert next_due(series) == date(2027, 4, 1) and status_of(series, date(2027, 3, 31)) == "active"
    assert annual_cost(series) == 14200 * 10


def test_ten_instalments_learned_from_two_years_without_a_hint():
    dates = [date(y, m, 1) for y in (2025, 2026) for m in (1, 4, 5, 6, 7, 8, 9, 10, 11, 12)]
    [series] = detect(pays(sorted(dates), 14200), as_of=date(2027, 1, 15))
    assert series.skip_months == (2, 3)


def test_minimum_occurrences_per_cadence():
    assert detect(pays(monthly(14, 2), 999), as_of=date(2026, 3, 1)) == []
    assert len(detect(pays(monthly(14, 3), 999), as_of=date(2026, 4, 1))) == 1
    assert detect(pays(every(7, 3), 250), as_of=date(2026, 2, 1)) == []


def test_calendar_projection_skips_months():
    [series] = detect(pays(monthly(15, 3), 1000), as_of=date(2026, 3, 31))
    assert due_dates(series, date(2026, 4, 1), date(2026, 6, 30)) == [
        date(2026, 4, 15),
        date(2026, 5, 15),
        date(2026, 6, 15),
    ]


def test_five_thousand_transactions_are_detected_quickly():
    import time

    payments = []
    for m in range(120):  # 120 merchants of 24 monthly payments, grouped as the specialist does
        payments.append(pays(monthly(1 + m % 28, 24), 500 + m))
    big = [  # one merchant with 2,000 irregular purchases: the worst case for amount groups
        Payment(f"b{i}", date(2024, 1, 1) + timedelta(days=(i * 7919) % 700), 300 + (i * 37) % 4000)
        for i in range(2000)
    ]
    start = time.perf_counter()
    for group in payments:
        assert len(detect(group, as_of=date(2027, 12, 31))) == 1
    detect(big, as_of=date(2027, 12, 31))
    assert sum(len(g) for g in payments) + len(big) >= 4880
    assert time.perf_counter() - start < 2.0
