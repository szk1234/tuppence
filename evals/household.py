"""A synthetic UK household: 12 to 24 months of bank and card history (spec §14.3).

Everything is invented: the merchants, the amounts and the people. Each transaction carries
the category it really belongs to, and the household's commitments are listed with what
the Commitments specialist should find, so the eval can score both. The same generator
writes the three-month Starling-style CSV the browser test uploads.

    uv run python -m evals.household --write-fixture
"""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import random
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "statements"
    / "history"
    / "starling-3-months.csv"
)


@dataclass(frozen=True)
class SynthTxn:
    account: str  # "current", "card" or "savings"
    date: date
    amount_pence: int  # money in positive, money out negative
    merchant: str
    category_id: str  # what it really is
    bank_type: str = "CARD"
    bank_category: str = ""


@dataclass(frozen=True)
class ExpectedCommitment:
    merchant: str
    account: str
    cadence: str
    amount_pence: int
    status: str = "active"
    flags: frozenset[str] = frozenset()


@dataclass
class Household:
    start: date
    end: date
    transactions: list[SynthTxn] = field(default_factory=list)
    commitments: list[ExpectedCommitment] = field(default_factory=list)


def _month_starts(start: date, end: date) -> list[date]:
    out, cursor = [], date(start.year, start.month, 1)
    while cursor <= end:
        out.append(cursor)
        cursor = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
    return out


def _on(month: date, day: int) -> date:
    return month.replace(day=min(day, calendar.monthrange(month.year, month.month)[1]))


def _last_working_day(month: date) -> date:
    day = _on(month, 31)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _every(first: date, days: int, end: date) -> list[date]:
    out, day = [], first
    while day <= end:
        out.append(day)
        day += timedelta(days=days)
    return out


def generate(*, months: int = 12, seed: int = 7, end: date = date(2026, 10, 31)) -> Household:
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not security
    first_month = _month_starts(end - timedelta(days=31 * (months - 1)), end)[0]
    h = Household(start=first_month, end=end)
    add = h.transactions.append
    month_list = [m for m in _month_starts(first_month, end) if m <= end]

    def inside(day: date) -> bool:
        return h.start <= day <= h.end

    for i, m in enumerate(month_list):
        add(
            SynthTxn(
                "current",
                _last_working_day(m),
                245000,
                "Acme Payroll Ltd Salary",
                "income.salary",
                "BANK CREDIT",
                "INCOME",
            )
        )
        add(
            SynthTxn(
                "current",
                _on(m, 1),
                -115000,
                "Homestead Lettings",
                "housing.rent",
                "STANDING ORDER",
                "BILLS",
            )
        )
        if m.month not in (2, 3):
            add(
                SynthTxn(
                    "current",
                    _on(m, 1),
                    -14200,
                    "Northfield Council Council Tax",
                    "housing.council-tax",
                    "DIRECT DEBIT",
                    "BILLS",
                )
            )
        add(
            SynthTxn(
                "current", _on(m, 15), -3115, "City Water", "housing.water", "DIRECT DEBIT", "BILLS"
            )
        )
        add(
            SynthTxn(
                "current",
                _on(m, 12),
                -9600,
                "Brightspark Energy",
                "housing.energy",
                "DIRECT DEBIT",
                "BILLS",
            )
        )
        broadband = 3550 if i >= months // 2 else 3200
        add(
            SynthTxn(
                "current",
                _on(m, 20),
                -broadband,
                "Fibrenet Broadband",
                "housing.broadband",
                "DIRECT DEBIT",
                "BILLS",
            )
        )
        add(
            SynthTxn(
                "current",
                _on(m, 1),
                -1454,
                "TV Licensing",
                "housing.tv-licence",
                "DIRECT DEBIT",
                "BILLS",
            )
        )
        add(
            SynthTxn(
                "current",
                _on(m, 8),
                -4820,
                "Shield Car Insurance",
                "transport.car.insurance",
                "DIRECT DEBIT",
                "TRANSPORT",
            )
        )
        add(
            SynthTxn(
                "current",
                _on(m, 26),
                -18900,
                "Roadstar Finance",
                "transport.car.finance",
                "DIRECT DEBIT",
                "TRANSPORT",
            )
        )
        add(
            SynthTxn(
                "current",
                _on(m, 1),
                -1663,
                "DVLA Vehicle Tax",
                "transport.car.road-tax",
                "DIRECT DEBIT",
                "TRANSPORT",
            )
        )
        add(
            SynthTxn(
                "current",
                _on(m, 3),
                -64000,
                "Sunnydays Nursery",
                "children.childcare",
                "DIRECT DEBIT",
                "FAMILY",
            )
        )
        add(
            SynthTxn(
                "current",
                _on(m, 10),
                -2800,
                "Splash Swim Club",
                "children.activities",
                "DIRECT DEBIT",
                "FAMILY",
            )
        )
        if i < max(2, months - 5):
            add(
                SynthTxn(
                    "current",
                    _on(m, 5),
                    -2500,
                    "Flexfit Gym",
                    "health.fitness",
                    "DIRECT DEBIT",
                    "LIFESTYLE",
                )
            )
        streamly = 1199 if i >= months // 2 else 999
        add(SynthTxn("card", _on(m, 14), -streamly, "Streamly", "subscriptions.tv-streaming"))
        add(
            SynthTxn(
                "current",
                _on(m, 3),
                -1099,
                "Tunewave Music",
                "subscriptions.music",
                "DIRECT DEBIT",
                "ENTERTAINMENT",
            )
        )
        add(SynthTxn("card", _on(m, 20), -1199, "Melodia Premium", "subscriptions.music"))
        if i == 2:
            add(SynthTxn("card", _on(m, 27), -99, "Cloudbox Storage", "subscriptions.software"))
        if i >= 3:
            add(SynthTxn("card", _on(m, 27), -799, "Cloudbox Storage", "subscriptions.software"))
        add(
            SynthTxn(
                "current",
                _on(m, 28),
                -20000,
                "Transfer to Rainy Day",
                "transfers.between-accounts",
                "FASTER PAYMENT",
                "TRANSFERS",
            )
        )
        add(
            SynthTxn(
                "savings",
                _on(m, 28),
                20000,
                "From current account",
                "transfers.between-accounts",
                "FASTER PAYMENT",
                "TRANSFERS",
            )
        )
        add(
            SynthTxn(
                "current",
                _on(m, 7),
                -5000,
                "Cash machine High St",
                "transfers.cash",
                "CASH",
                "CASH",
            )
        )
        if i % 6 == 2:
            add(
                SynthTxn(
                    "current",
                    _on(m, 18),
                    -4000,
                    "Pat Example",
                    "gifts.presents",
                    "FASTER PAYMENT",
                    "TRANSFERS",
                )
            )
        if i % 3 == 1:
            add(
                SynthTxn(
                    "current",
                    _on(m, 9),
                    -6200,
                    "Lifeshield Protection",
                    "financial.protection",
                    "DIRECT DEBIT",
                    "BILLS",
                )
            )
        for _ in range(rng.randint(2, 4)):
            add(
                SynthTxn(
                    "card",
                    _on(m, rng.randint(1, 28)),
                    -rng.randint(280, 650),
                    "Little Cafe",
                    "food.eating-out",
                    "CARD",
                    "EATING_OUT",
                )
            )
        add(
            SynthTxn(
                "card",
                _on(m, rng.randint(1, 28)),
                -rng.randint(1800, 3200),
                "Pizza Pronto",
                "food.takeaway",
                "CARD",
                "EATING_OUT",
            )
        )
        add(
            SynthTxn(
                "card",
                _on(m, rng.randint(1, 28)),
                -rng.randint(1200, 4000),
                "Northline Rail",
                "transport.public",
                "CARD",
                "TRANSPORT",
            )
        )
        if i % 2 == 0:
            add(
                SynthTxn(
                    "card",
                    _on(m, rng.randint(1, 28)),
                    -rng.randint(400, 1500),
                    "Harbour Pharmacy",
                    "health.pharmacy",
                    "CARD",
                    "HEALTH",
                )
            )
            add(
                SynthTxn(
                    "card",
                    _on(m, rng.randint(1, 28)),
                    -rng.randint(1500, 3000),
                    "City Cinema",
                    "entertainment.going-out",
                    "CARD",
                    "ENTERTAINMENT",
                )
            )
        else:
            add(
                SynthTxn(
                    "card",
                    _on(m, rng.randint(1, 28)),
                    -rng.randint(800, 2500),
                    "Page and Spine Books",
                    "entertainment.hobbies",
                    "CARD",
                    "SHOPPING",
                )
            )
        add(
            SynthTxn(
                "card",
                _on(m, rng.randint(1, 28)),
                -rng.randint(2500, 6000),
                "Valuemart",
                "food.groceries",
                "CARD",
                "GROCERIES",
            )
        )
    if months >= 6:
        add(
            SynthTxn(
                "card",
                _on(month_list[months // 3], 11),
                -42000,
                "Sunny Travel Flights",
                "holidays.travel",
                "CARD",
                "HOLIDAYS",
            )
        )
    for day in _every(_first_weekday(first_month, 5), 7, end):  # Saturdays
        add(
            SynthTxn(
                "current",
                day,
                -rng.randint(4000, 12000),
                "Greenbasket Stores",
                "food.groceries",
                "CARD",
                "GROCERIES",
            )
        )
    for day in _every(_first_weekday(first_month, 0), 7, end):  # Mondays
        add(SynthTxn("card", day, -250, "Daily News Digital", "subscriptions.news"))
    for day in _every(first_month + timedelta(days=8), 14, end):
        add(
            SynthTxn(
                "current",
                day,
                -1500,
                "Sparkle Window Cleaning",
                "housing.repairs",
                "STANDING ORDER",
                "BILLS",
            )
        )
    for day in _every(first_month + timedelta(days=4), 28, end):
        add(
            SynthTxn(
                "current",
                day,
                -1840,
                "Happy Paws Pet Insurance",
                "pets.insurance",
                "DIRECT DEBIT",
                "PETS",
            )
        )
    for day in _every(_first_weekday(first_month, 0) + timedelta(days=7), 28, end):
        add(
            SynthTxn(
                "current",
                day,
                10240,
                "HMRC Child Benefit",
                "income.benefits",
                "BANK CREDIT",
                "INCOME",
            )
        )
    for day in _every(first_month + timedelta(days=10), rng.randint(10, 14), end):
        add(
            SynthTxn(
                "card",
                day,
                -rng.randint(4500, 7500),
                "Swiftfuel Service Station",
                "transport.car.fuel",
                "CARD",
                "TRANSPORT",
            )
        )
    _card_repayments(h)
    h.transactions.sort(key=lambda t: (t.date, t.account, t.merchant))
    h.transactions = [t for t in h.transactions if inside(t.date)]
    h.commitments = _expected(months)
    return h


def _first_weekday(start: date, weekday: int) -> date:
    return start + timedelta(days=(weekday - start.weekday()) % 7)


def _card_repayments(h: Household) -> None:
    """Pay off each month's card spending on the 25th of the next month."""
    spent: dict[tuple[int, int], int] = {}
    for t in h.transactions:
        if t.account == "card" and t.amount_pence < 0:
            spent[(t.date.year, t.date.month)] = spent.get((t.date.year, t.date.month), 0) - (
                t.amount_pence
            )
    for (y, mo), pence in sorted(spent.items()):
        day = date(y + mo // 12, mo % 12 + 1, 25)
        h.transactions.append(
            SynthTxn(
                "current",
                day,
                -pence,
                "Example Card Co",
                "transfers.card-repayment",
                "DIRECT DEBIT",
                "TRANSFERS",
            )
        )
        h.transactions.append(
            SynthTxn(
                "card",
                day,
                pence,
                "Payment received - thank you",
                "transfers.card-repayment",
                "PAYMENT",
                "",
            )
        )


def _expected(months: int) -> list[ExpectedCommitment]:
    half = frozenset({"price_rise"})
    out = [
        ExpectedCommitment("Homestead Lettings", "current", "monthly", 115000),
        ExpectedCommitment("City Water", "current", "monthly", 3115),
        ExpectedCommitment("Brightspark Energy", "current", "monthly", 9600),
        ExpectedCommitment("Fibrenet Broadband", "current", "monthly", 3550, flags=half),
        ExpectedCommitment("TV Licensing", "current", "monthly", 1454),
        ExpectedCommitment("Shield Car Insurance", "current", "monthly", 4820),
        ExpectedCommitment("Roadstar Finance", "current", "monthly", 18900),
        ExpectedCommitment("DVLA Vehicle Tax", "current", "monthly", 1663),
        ExpectedCommitment("Sunnydays Nursery", "current", "monthly", 64000),
        ExpectedCommitment("Splash Swim Club", "current", "monthly", 2800),
        ExpectedCommitment(
            "Flexfit Gym", "current", "monthly", 2500, status="lapsed", flags=frozenset({"lapsed"})
        ),
        ExpectedCommitment("Streamly", "card", "monthly", 1199, flags=half),
        ExpectedCommitment(
            "Tunewave Music", "current", "monthly", 1099, flags=frozenset({"duplicate"})
        ),
        ExpectedCommitment(
            "Melodia Premium", "card", "monthly", 1199, flags=frozenset({"duplicate"})
        ),
        ExpectedCommitment(
            "Cloudbox Storage", "card", "monthly", 799, flags=frozenset({"free_trial_converted"})
        ),
        ExpectedCommitment("Daily News Digital", "card", "weekly", 250),
        ExpectedCommitment("Sparkle Window Cleaning", "current", "fortnightly", 1500),
        ExpectedCommitment("Happy Paws Pet Insurance", "current", "four_weekly", 1840),
        ExpectedCommitment("Lifeshield Protection", "current", "quarterly", 6200),
    ]
    if months >= 12:
        out.append(
            ExpectedCommitment("Northfield Council Council Tax", "current", "monthly", 14200)
        )
    return out


def starling_csv(h: Household, *, account: str = "current") -> str:
    """The account's transactions as a Starling export (the `uk-banks` pack's layout)."""
    rows = [t for t in h.transactions if t.account == account]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "Date",
            "Counter Party",
            "Reference",
            "Type",
            "Amount (GBP)",
            "Balance (GBP)",
            "Spending Category",
            "Notes",
        ]
    )
    balance = 250000
    for n, t in enumerate(rows, start=1):
        balance += t.amount_pence
        writer.writerow(
            [
                t.date.strftime("%d/%m/%Y"),
                t.merchant,
                f"REF {n:04d}",
                t.bank_type,
                f"{t.amount_pence / 100:.2f}",
                f"{balance / 100:.2f}",
                t.bank_category,
                "",
            ]
        )
    return out.getvalue()


def three_month_fixture() -> str:
    """The browser test's statement: August to October 2026 on one current account, with a
    bakery the oracle can't place (so the person corrects it) and a Streamly subscription."""
    h = generate(months=3, seed=11, end=date(2026, 10, 31))
    current = [t for t in h.transactions if t.account == "current"]
    extra = [
        SynthTxn(
            "current",
            date(2026, m, 14),
            -999,
            "Streamly",
            "subscriptions.tv-streaming",
            "CARD",
            "ENTERTAINMENT",
        )
        for m in (8, 9, 10)
    ]
    extra += [
        SynthTxn(
            "current",
            date(2026, m, d),
            -p,
            "Sunrise Bakery",
            "food.eating-out",
            "CARD",
            "EATING_OUT",
        )
        for m, d, p in ((8, 6, 420), (9, 9, 385), (10, 12, 450))
    ]
    h.transactions = sorted(current + extra, key=lambda t: (t.date, t.merchant))
    return starling_csv(h)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.household")
    parser.add_argument(
        "--write-fixture",
        action="store_true",
        help=f"write {FIXTURE.relative_to(FIXTURE.parents[3])}",
    )
    args = parser.parse_args(argv)
    if args.write_fixture:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(three_month_fixture(), encoding="utf-8")
        print(f"wrote {FIXTURE}")
    else:
        sys.stdout.write(three_month_fixture())
    return 0


if __name__ == "__main__":
    sys.exit(main())
