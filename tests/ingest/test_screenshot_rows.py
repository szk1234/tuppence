"""Screenshots: what is held back from the reader, and that no transaction is lost silently.

Banking apps list pending and "Today" rows without a date, so only the lines above the first
line with a date or an amount count as the header. All values invented.
"""

import datetime as dt
import json
from pathlib import Path

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
