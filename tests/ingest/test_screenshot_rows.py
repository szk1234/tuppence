"""Screenshots: what is held back from the reader, and that no transaction is lost silently.

Banking apps list pending and "Today" rows without a date, so only the lines above the first
line with a date or an amount count as the header. All values invented.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from ingest.helpers import budget, use_local_model
from tuppence.ingest.identify import identify
from tuppence.ingest.parse import ReaderLimits, parse_document
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack
from tuppence.ingest.textprep import pages_document


def shot(rows):
    return pages_document([rows], sha256="x", kind="image")


def texts(doc, refs):
    by_ref = doc.by_ref()
    return [by_ref[r].text for r in refs]


def test_pending_rows_above_dated_rows_are_read():
    doc = shot(
        [
            "Current account",
            "Pending",
            "GREENBASKET STORES -£3.40",
            "LITTLE CAFE -£12.80",
            "5 Oct CITY WATER -£31.15",
            "4 Oct ACME PAYROLL LTD +£250.00",
        ]
    )
    assert texts(doc, doc.preamble_refs) == ["Current account", "Pending"]
    assert texts(doc, doc.data_refs)[:2] == ["GREENBASKET STORES -£3.40", "LITTLE CAFE -£12.80"]


def test_today_and_yesterday_rows_are_read():
    doc = shot(
        [
            "Today",
            "GREENBASKET STORES -£3.40",
            "Yesterday",
            "LITTLE CAFE -£12.80",
            "3 Oct HOMEWARE DIRECT -£43.27",
        ]
    )
    assert texts(doc, doc.preamble_refs) == ["Today"]
    assert "LITTLE CAFE -£12.80" in texts(doc, doc.data_refs)


def test_names_and_account_details_are_held_back_wherever_they_are():
    doc = shot(
        [
            "Alex Example",
            "Sort code 12-34-56 Account 12345678",
            "5 Oct GREENBASKET STORES -£3.40",
            "Alex Example",
            "Card ending 4242",
            "6 Oct LITTLE CAFE -£12.80",
        ]
    )
    assert texts(doc, doc.data_refs) == [
        "5 Oct GREENBASKET STORES -£3.40",
        "6 Oct LITTLE CAFE -£12.80",
    ]


def parse_shot(services, rows):
    window = use_local_model(services)
    pack = load_bank_pack()
    registry = LayoutRegistry(pack)
    doc = shot(rows)
    evidence = identify(doc, pack=pack, registry=registry, key=b"k")
    return parse_document(
        doc,
        Path("unused.png"),
        evidence,
        "current",
        registry=registry,
        llm=services.llm,
        run=budget(),
        context_window=window,
        today=dt.date(2026, 10, 9),
        limits=ReaderLimits(),
    )


def test_a_held_back_line_with_an_amount_needs_review(ingest_env):
    services, scripted = ingest_env
    out = parse_shot(
        services,
        [
            "5 Oct GREENBASKET STORES -£3.40",
            "6 Oct FPO PAT EXAMPLE 20-00-00 87654321 -£10.00",
            "7 Oct LITTLE CAFE -£12.80",
        ],
    )
    sent = json.dumps(scripted.requests)
    assert "87654321" not in sent and "PAT EXAMPLE" not in sent
    assert len(out.parsed.rows) == 2
    assert any("held back" in e for e in out.errors)
    assert all("87654321" not in e and "PAT EXAMPLE" not in e for e in out.errors)


def test_a_screenshot_with_nothing_held_back_reads_cleanly(ingest_env):
    services, _ = ingest_env
    out = parse_shot(
        services,
        ["Alex Example", "Current account", "5 Oct GREENBASKET STORES -£3.40", "Card ending 4242"],
    )
    assert out.errors == [] and len(out.parsed.rows) == 1


def test_undated_rows_are_sent_with_how_to_date_them(ingest_env):
    services, scripted = ingest_env
    parse_shot(services, ["Pending", "GREENBASKET STORES -£3.40", "5 Oct CITY WATER -£31.15"])
    user = scripted.requests[0]["messages"][-1]["content"]
    assert "GREENBASKET STORES -£3.40" in user
    assert "no date of its own is still a transaction" in user


def test_balance_lines_are_held_back_without_a_coverage_error(ingest_env):
    services, scripted = ingest_env
    rows = [
        "Current account",
        "Balance £1,234.56",
        "Available to spend £1,184.56",
        "Pending",
        "GREENBASKET STORES -£3.40",
        "5 Oct CITY WATER -£31.15",
        "Overdraft limit £500.00",
    ]
    doc = shot(rows)
    assert texts(doc, doc.preamble_refs) == [
        "Current account",
        "Balance £1,234.56",
        "Available to spend £1,184.56",
        "Pending",
        "Overdraft limit £500.00",
    ]
    out = parse_shot(services, rows)
    sent = json.dumps(scripted.requests)
    assert "1,234.56" not in sent and "1,184.56" not in sent and "500.00" not in sent
    assert not any("held back" in e for e in out.errors)


# The app header layouts the re-review found sending the balance (R-M3-17).
APP_HEADERS = {
    "label under figure": ["Current Account", "£1,184.56", "Available balance", "Today"],
    "figure then 'available'": ["Main account", "£1,184.56 available", "Today"],
    "label above figure": ["Balance", "£1,184.56"],
    "no thousands comma": ["Current Account", "Balance £1184.56"],
    "spending balance": ["Spending balance £1,184.56"],
    "two figures": ["Balance £1,184.56 Available £1,084.56"],
    "currency code": ["Balance: GBP 1,184.56"],
}


@pytest.mark.parametrize("header", APP_HEADERS.values(), ids=APP_HEADERS.keys())
def test_app_balance_headers_are_held_back_without_a_coverage_error(ingest_env, header):
    services, scripted = ingest_env
    rows = [*header, "LITTLE CAFE -£12.80", "5 Oct CITY WATER -£31.15"]
    doc = shot(rows)
    assert texts(doc, doc.data_refs) == ["LITTLE CAFE -£12.80", "5 Oct CITY WATER -£31.15"]
    out = parse_shot(services, rows)
    sent = json.dumps(scripted.requests, ensure_ascii=False)
    assert "1,184.56" not in sent and "1184.56" not in sent and "1,084.56" not in sent
    assert "LITTLE CAFE -£12.80" in sent and "CITY WATER -£31.15" in sent
    assert not any("held back" in e for e in out.errors)


def test_a_masked_card_ending_is_never_sent(ingest_env):
    services, scripted = ingest_env
    rows = [
        "Current account",
        "LITTLE CAFE -£12.80",
        "Card •••• 4242",
        "Visa Debit ...4242",
        "Card …4242",
        "5 Oct CITY WATER -£31.15",
    ]
    doc = shot(rows)
    assert texts(doc, doc.data_refs) == ["LITTLE CAFE -£12.80", "5 Oct CITY WATER -£31.15"]
    parse_shot(services, rows)
    assert "4242" not in json.dumps(scripted.requests, ensure_ascii=False)


# N6 (re-review 3): balance labels and figure-only lines next to two-line rows. The balance is
# never sent, and a row's amount is never lost without a "held back" error.
BALANCE_FIGURES = ("1,184.56", "1,500.00", "1,084.56")
TWO_LINE_SCREENS = {
    "h1 two balances, figure above label": (
        ["£1,184.56", "Available balance", "£1,500.00", "Balance", "Today", "LITTLE CAFE -£12.80"],
        True,
    ),
    "h2 balance and spent today, figure above label": (
        ["£1,184.56", "Balance", "£20.00", "Spent today", "Today", "LITTLE CAFE -£12.80"],
        True,
    ),
    "h2b both figures read first": (
        ["£1,184.56", "£20.00", "Balance", "Spent today", "Today", "LITTLE CAFE -£12.80"],
        True,
    ),
    "h3 figure above label, amount-first row below": (
        [
            "Current Account",
            "£1,184.56",
            "Available balance",
            "-£12.80",
            "Little Cafe",
            "5 Oct CITY WATER -£31.15",
        ],
        False,
    ),
    "h4 screen cut after a label, two-line row above": (
        ["5 Oct CITY WATER -£31.15", "Little Cafe", "-£12.80", "Available balance"],
        False,
    ),
    "h7 two-line row, then label over figure": (
        ["5 Oct CITY WATER -£31.15", "Little Cafe", "-£12.80", "Balance", "£1,184.56"],
        False,
    ),
}


@pytest.mark.parametrize(("rows", "clean"), TWO_LINE_SCREENS.values(), ids=TWO_LINE_SCREENS.keys())
def test_a_balance_never_takes_a_rows_amount_silently(ingest_env, rows, clean):
    services, scripted = ingest_env
    out = parse_shot(services, rows)
    sent = json.dumps(scripted.requests, ensure_ascii=False)
    assert not any(figure in sent for figure in BALANCE_FIGURES)
    held = any("held back" in e for e in out.errors)
    if "-£12.80" not in sent:  # the Little Cafe row's amount was held back: say so
        assert held
    assert held != clean
