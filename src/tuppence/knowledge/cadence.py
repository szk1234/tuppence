"""Regular payments, found from the gaps between them (spec §8.2 Commitments).

Pure functions over one merchant's payments from one account. A run of payments is a
series when its gaps fit one cadence (weekly, fortnightly, 4-weekly, monthly, quarterly or
annual), allowing for missed payments. Monthly and 4-weekly are told apart by the day of
the month: monthly payments keep it, 4-weekly ones drift. Price changes split the amounts
into steps; a small first payment before the full price is a free trial that converted.
"""

from __future__ import annotations

import calendar
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Literal

Cadence = Literal["weekly", "fortnightly", "four_weekly", "monthly", "quarterly", "annual"]
CADENCES: tuple[Cadence, ...] = (
    "weekly",
    "fortnightly",
    "four_weekly",
    "monthly",
    "quarterly",
    "annual",
)
NOMINAL_DAYS: dict[Cadence, float] = {
    "weekly": 7,
    "fortnightly": 14,
    "four_weekly": 28,
    "monthly": 30.44,
    "quarterly": 91.31,
    "annual": 365.25,
}
TOLERANCE_DAYS: dict[Cadence, float] = {
    "weekly": 1.5,
    "fortnightly": 2.5,
    "four_weekly": 2.0,
    "monthly": 4.5,
    "quarterly": 10,
    "annual": 21,
}
PER_YEAR: dict[Cadence, int] = {
    "weekly": 52,
    "fortnightly": 26,
    "four_weekly": 13,
    "monthly": 12,
    "quarterly": 4,
    "annual": 1,
}
MIN_OCCURRENCES: dict[Cadence, int] = {
    "weekly": 4,
    "fortnightly": 3,
    "four_weekly": 3,
    "monthly": 3,
    "quarterly": 3,
    "annual": 2,
}
LAPSE_AFTER_MISSED: dict[Cadence, int] = {
    "weekly": 3,
    "fortnightly": 2,
    "four_weekly": 2,
    "monthly": 2,
    "quarterly": 1,
    "annual": 1,
}
MAX_STEPS = 3  # a gap may cover at most two missed payments
DAY_JITTER = 4  # a payment day moved by a weekend or bank holiday


@dataclass(frozen=True)
class Payment:
    transaction_id: str
    date: date
    amount_pence: int  # money out, as a positive number


@dataclass(frozen=True)
class PriceStep:
    since: date
    amount_pence: int


@dataclass(frozen=True)
class Series:
    cadence: Cadence
    payments: tuple[Payment, ...]
    expected_amount_pence: int
    expected_day: int | None  # monthly-ish: day of month (0 = the last day); else a weekday
    skip_months: tuple[int, ...]  # months it is never paid in (council tax: Feb and Mar)
    price_steps: tuple[PriceStep, ...]  # each change of price, oldest first
    missed: tuple[date, ...]  # expected dates with no payment, within the history
    trial: Payment | None  # a small first payment before the full price
    varies: bool  # the amount moves around (a metered bill), so no price steps

    @property
    def first(self) -> Payment:
        return self.payments[0]

    @property
    def last(self) -> Payment:
        return self.payments[-1]


def consistent_day(dates: Sequence[date]) -> int | None:
    """Median day of month, 0 when the payments sit on the month end, None when they drift."""
    if len({(d.year, d.month) for d in dates}) < 2:
        return None
    days = [d.day for d in dates]
    if len(set(days)) > 1 and all(
        calendar.monthrange(d.year, d.month)[1] - d.day <= DAY_JITTER for d in dates
    ):
        return 0
    median = int(statistics.median_high(days))
    return median if all(abs(day - median) <= DAY_JITTER for day in days) else None


def _steps(gaps: Sequence[int], cadence: Cadence) -> list[int] | None:
    out: list[int] = []
    for gap in gaps:
        k = round(gap / NOMINAL_DAYS[cadence])
        if (
            k < 1
            or k > MAX_STEPS
            or abs(gap - k * NOMINAL_DAYS[cadence]) > (TOLERANCE_DAYS[cadence] * k)
        ):
            return None
        out.append(k)
    return out


def fit_cadence(dates: Sequence[date]) -> tuple[Cadence, list[int]] | None:
    """The cadence these dates follow and, per gap, how many periods it spans."""
    gaps = [(b - a).days for a, b in pairwise(sorted(dates))]
    if not gaps:
        return None
    fits: list[tuple[float, Cadence, list[int]]] = []
    for cadence in CADENCES:
        steps = _steps(gaps, cadence)
        if steps is not None and steps.count(1) / len(steps) >= 0.6:
            fits.append((steps.count(1) / len(steps), cadence, steps))
    if not fits:
        return None
    names = {c for _, c, _ in fits}
    if {"monthly", "four_weekly"} <= names:
        keep = "monthly" if consistent_day(dates) is not None else "four_weekly"
        fits = [f for f in fits if f[1] not in ({"monthly", "four_weekly"} - {keep})]
    best = max(fits, key=lambda f: (f[0], -NOMINAL_DAYS[f[1]]))
    return best[1], best[2]


def add_months(day: date, months: int, expected_day: int | None) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    wanted = day.day if expected_day is None else (last if expected_day == 0 else expected_day)
    return date(year, month, min(wanted, last))


def step(day: date, cadence: Cadence, expected_day: int | None, skip: Iterable[int] = ()) -> date:
    """The next due date after a payment on `day`."""
    if cadence in ("weekly", "fortnightly", "four_weekly"):
        return day + timedelta(days=int(NOMINAL_DAYS[cadence]))
    months = {"monthly": 1, "quarterly": 3, "annual": 12}[cadence]
    skipped = set(skip)
    nxt = add_months(day, months, expected_day)
    for _ in range(12):
        if nxt.month not in skipped:
            break
        nxt = add_months(nxt, months, expected_day)
    return nxt


def _price_levels(payments: Sequence[Payment]) -> tuple[list[PriceStep], bool]:
    """Runs of (nearly) the same amount. `varies` when amounts move around rather than step."""
    runs: list[list[Payment]] = []
    for p in payments:
        if runs:
            anchor = runs[-1][0].amount_pence
            if abs(p.amount_pence - anchor) <= max(50, round(anchor * 0.03)):
                runs[-1].append(p)
                continue
        runs.append([p])
    one_offs = sum(1 for run in runs[:-1] if len(run) == 1)
    varies = one_offs >= 2 or len(runs) > 4
    return [PriceStep(run[0].date, run[-1].amount_pence) for run in runs], varies


def skip_months_for(
    payments: Sequence[Payment], *, as_of: date, expected_gaps: frozenset[int] = frozenset()
) -> tuple[int, ...]:
    """Months a monthly payment is never made in, when that is the pattern rather than a lapse:
    seen in two years of history, or the months `expected_gaps` names (UK council tax is often
    paid in 10 instalments, April to January)."""
    paid = {p.date.month for p in payments}
    covered: dict[int, int] = {}
    cursor = date(payments[0].date.year, payments[0].date.month, 1)
    while cursor <= as_of:
        covered[cursor.month] = covered.get(cursor.month, 0) + 1
        cursor = add_months(cursor, 1, 1)
    missing = {m for m in covered if m not in paid}
    if not missing or len(missing) > 3:
        return ()
    if all(covered[m] >= 2 for m in missing) or missing <= expected_gaps:
        return tuple(sorted(missing))
    return ()


def _series(
    cadence: Cadence,
    steps: list[int],
    payments: Sequence[Payment],
    *,
    as_of: date,
    trial: Payment | None,
    expected_gaps: frozenset[int],
) -> Series:
    dates = [p.date for p in payments]
    if cadence in ("monthly", "quarterly", "annual"):
        expected_day = consistent_day(dates)
        if expected_day is None:
            expected_day = payments[-1].date.day
    else:
        expected_day = payments[-1].date.weekday()
    skip = (
        skip_months_for(payments, as_of=as_of, expected_gaps=expected_gaps)
        if (cadence == "monthly")
        else ()
    )
    missed: list[date] = []
    for (a, _), k in zip(pairwise(payments), steps, strict=True):
        due = a.date
        for _ in range(k - 1):
            due = step(due, cadence, expected_day)
            if due.month not in skip:
                missed.append(due)
    levels, varies = _price_levels(payments)
    if varies:
        expected = int(statistics.median([p.amount_pence for p in payments[-3:]]))
        price_steps: tuple[PriceStep, ...] = ()
    else:
        expected = levels[-1].amount_pence
        price_steps = tuple(levels)
    return Series(
        cadence,
        tuple(payments),
        expected,
        expected_day,
        skip,
        price_steps,
        tuple(missed),
        trial,
        varies,
    )


def _groups(payments: Sequence[Payment], tolerance: float) -> list[list[Payment]]:
    """Candidate runs: everything together when the amounts are close enough to be one
    price that changed, else amount clusters (anchored on each cluster's smallest)."""
    amounts = [p.amount_pence for p in payments]
    if max(amounts) <= min(amounts) * 1.6:
        return [list(payments)]
    clusters: list[list[Payment]] = []
    for p in sorted(payments, key=lambda p: (p.amount_pence, p.date)):
        if clusters and p.amount_pence <= clusters[-1][0].amount_pence * (1 + tolerance):
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [sorted(c, key=lambda p: p.date) for c in clusters]


def _exact_groups(payments: Sequence[Payment]) -> list[list[Payment]]:
    """Payments of exactly the same amount (within 1% or 10p): subscriptions charge exactly."""
    groups: list[list[Payment]] = []
    for p in sorted(payments, key=lambda p: (p.amount_pence, p.date)):
        anchor = groups[-1][0].amount_pence if groups else None
        if anchor is not None and p.amount_pence - anchor <= max(10, round(anchor * 0.01)):
            groups[-1].append(p)
        else:
            groups.append([p])
    return [sorted(g, key=lambda p: p.date) for g in groups if len(g) >= 2]


def detect(
    payments: Sequence[Payment],
    *,
    as_of: date,
    tolerance: float = 0.15,
    min_occurrences: Mapping[Cadence, int] = MIN_OCCURRENCES,
    expected_gaps: frozenset[int] = frozenset(),
) -> list[Series]:
    """Every regular series in one merchant's payments from one account."""
    ordered = sorted(payments, key=lambda p: (p.date, p.transaction_id))
    if len(ordered) < 2:
        return []
    trial: Payment | None = None
    rest = ordered
    later = [p.amount_pence for p in ordered[1:]]
    if ordered[0].amount_pence <= max(100, statistics.median(later) * 0.2):
        trial, rest = ordered[0], ordered[1:]
    found: list[Series] = []
    for group in _groups(rest, tolerance):
        candidates = [group]
        if fit_cadence([p.date for p in group]) is None:
            candidates = _exact_groups(group)  # a subscription hidden among one-off purchases
        for candidate in candidates:
            fitted = fit_cadence([p.date for p in candidate])
            if fitted is None or len(candidate) < min_occurrences.get(fitted[0], 2):
                continue
            cadence, steps = fitted
            attached = None
            if trial is not None and candidate[0] is rest[0]:
                gap = (candidate[0].date - trial.date).days
                if 0 < gap <= NOMINAL_DAYS[cadence] * 1.5:
                    attached = trial
            found.append(
                _series(
                    cadence,
                    steps,
                    candidate,
                    as_of=as_of,
                    trial=attached,
                    expected_gaps=expected_gaps,
                )
            )
    return found


def missed_since(series: Series, as_of: date) -> list[date]:
    """Due dates after the last payment that have passed (allowing the usual slack)."""
    slack = timedelta(days=TOLERANCE_DAYS[series.cadence] + 2)
    out: list[date] = []
    due = step(series.last.date, series.cadence, series.expected_day, series.skip_months)
    while due + slack < as_of and len(out) < 24:
        out.append(due)
        due = step(due, series.cadence, series.expected_day, series.skip_months)
    return out


def status_of(series: Series, as_of: date) -> Literal["active", "lapsed"]:
    lapsed = len(missed_since(series, as_of)) >= LAPSE_AFTER_MISSED[series.cadence]
    return "lapsed" if lapsed else "active"


def next_due(series: Series) -> date:
    return step(series.last.date, series.cadence, series.expected_day, series.skip_months)


def due_dates(series: Series, start: date, end: date) -> list[date]:
    """Projected due dates between `start` and `end` (inclusive) for the calendar."""
    out: list[date] = []
    due = next_due(series)
    while due <= end and len(out) < 400:
        if due >= start:
            out.append(due)
        due = step(due, series.cadence, series.expected_day, series.skip_months)
    return out


def annual_cost(series: Series) -> int:
    per_year = PER_YEAR[series.cadence] - (
        len(series.skip_months) if series.cadence == "monthly" else 0
    )
    return series.expected_amount_pence * per_year
