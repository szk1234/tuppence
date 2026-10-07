"""Directions and balances of AI-read statements are settled on this device."""

import json

from ingest.helpers import parse_pages
from ingest.statements import HEAD, TABLE


def rows_of(out):
    return [(r.raw_description, r.amount_pence) for r in out.parsed.rows]


def test_dated_summary_box_and_a_first_row_credit(ingest_env):
    services, scripted = ingest_env
    page = [
        *HEAD,
        "Opening balance on 01/10/2026 £1,000.00",
        "Closing balance on 31/10/2026 £1,857.82",
        TABLE,
        "01/10/2026 ACME PAYROLL LTD 900.00 1,900.00",
        "03/10/2026 GREENBASKET STORES 42.18 1,857.82",
    ]
    out, _ = parse_pages(services, [page])
    assert out.errors == []
    assert rows_of(out) == [("ACME PAYROLL LTD", 90000), ("GREENBASKET STORES", -4218)]
    assert (out.parsed.opening_balance_pence, out.parsed.closing_balance_pence) == (
        100000,
        185782,
    )
    assert out.info["signs_repaired"] == ["P1L10"]
    sent = json.dumps(scripted.requests)
    assert "1,000.00" not in sent and "1,857.82\\n" not in sent.replace("957.82", "")


def test_first_row_credit_with_an_undated_summary(ingest_env):
    services, _ = ingest_env
    page = [
        *HEAD,
        "Opening balance 1,000.00",
        "Closing balance 1,857.82",
        TABLE,
        "01/10/2026 ACME PAYROLL LTD 900.00 1,900.00",
        "03/10/2026 GREENBASKET STORES 42.18 1,857.82",
    ]
    out, _ = parse_pages(services, [page])
    assert out.errors == [] and rows_of(out)[0] == ("ACME PAYROLL LTD", 90000)


def test_no_opening_balance_anywhere_is_reported_not_guessed(ingest_env):
    services, _ = ingest_env
    page = [
        *HEAD,
        TABLE,
        "01/10/2026 ACME PAYROLL LTD 900.00 1,900.00",
        "03/10/2026 GREENBASKET STORES 42.18 1,857.82",
    ]
    out, _ = parse_pages(services, [page])
    assert any("can't tell whether" in e for e in out.errors)


def test_a_chunk_that_starts_after_a_page_heading(ingest_env):
    services, _ = ingest_env
    from tuppence.ingest.parse import ReaderLimits

    p1 = [
        *HEAD,
        "Opening balance 1,000.00",
        "Closing balance 1,817.65",
        TABLE,
        "01/10/2026 Balance brought forward 1,000.00",
        "02/10/2026 SHOP 2 1.00 999.00",
        "03/10/2026 SHOP 3 1.00 998.00",
    ]
    p2 = [
        TABLE,
        "10/10/2026 ACME PAYROLL LTD 900.00 1,898.00",
        "12/10/2026 GREENBASKET STORES 80.35 1,817.65",
    ]
    out, _ = parse_pages(services, [p1, p2], limits=ReaderLimits(rows_per_chunk=5))
    assert out.errors == []
    assert ("ACME PAYROLL LTD", 90000) in rows_of(out)


def test_balances_printed_with_cr_on_a_current_account(ingest_env):
    services, _ = ingest_env
    page = [
        *HEAD,
        "Opening balance 1,000.00 CR",
        "Closing balance 1,857.82 CR",
        TABLE,
        "01/10/2026 Balance brought forward 1,000.00",
        "01/10/2026 ACME PAYROLL LTD 900.00 1,900.00",
        "03/10/2026 GREENBASKET STORES 42.18 1,857.82",
    ]
    out, _ = parse_pages(services, [page])
    assert out.errors == []
    assert (out.parsed.opening_balance_pence, out.parsed.closing_balance_pence) == (
        100000,
        185782,
    )


def test_an_overdrawn_opening_balance_in_brackets(ingest_env):
    services, _ = ingest_env
    page = [
        *HEAD,
        "Opening balance (100.00)",
        "Closing balance 757.82",
        TABLE,
        "01/10/2026 Balance brought forward (100.00)",
        "01/10/2026 ACME PAYROLL LTD 900.00 800.00",
        "03/10/2026 GREENBASKET STORES 42.18 757.82",
    ]
    out, _ = parse_pages(services, [page])
    assert out.errors == []
    assert out.parsed.opening_balance_pence == -10000


def test_a_card_balance_with_cr_is_a_credit(ingest_env):
    services, _ = ingest_env
    page = [
        "Card statement",
        "Statement for 29 Sep 2026 to 28 Oct 2026",
        "Previous balance 100.00 CR",
        "New balance 80.00 CR",
        "Date Description Amount",
        "30 Sep 2026 Greenbasket Stores 30.00",
        "02 Oct 2026 Payment received - thank you 90.00 CR",
        "05 Oct 2026 Northline Rail 80.00",
    ]
    out, _ = parse_pages(services, [page], "credit_card")
    assert (out.parsed.opening_balance_pence, out.parsed.closing_balance_pence) == (-10000, -8000)
    assert [r.amount_pence for r in out.parsed.rows] == [-3000, 9000, -8000]
    assert out.errors == []
