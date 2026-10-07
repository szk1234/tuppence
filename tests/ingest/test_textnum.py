import codecs
from datetime import date
from decimal import Decimal

import pytest

from tuppence.ingest.textnum import (
    decode_text,
    has_credit_marker,
    parse_date,
    parse_money,
    pounds,
    to_pence,
)


@pytest.mark.parametrize(
    "text,value",
    [
        ("42.18", Decimal("42.18")),
        ("-42.18", Decimal("-42.18")),
        ("£1,234.56", Decimal("1234.56")),
        ("-£12.50", Decimal("-12.50")),
        ("£-12.50", Decimal("-12.50")),
        ("12.50-", Decimal("-12.50")),
        ("(12.50)", Decimal("-12.50")),
        ("−12.50", Decimal("-12.50")),
        ("150.00 CR", Decimal("150.00")),
        ("150.00CR", Decimal("150.00")),
        ("12.50 DR", Decimal("-12.50")),
        ("+£250.00", Decimal("250.00")),
        ("1650", Decimal("1650")),
        ("12.5", Decimal("12.5")),
        (" £ 7 ", Decimal("7")),
    ],
)
def test_parse_money(text, value):
    assert parse_money(text) == value


@pytest.mark.parametrize(
    "text", ["", "   ", "abc", "12.345", "1.2.3", "(12.50", "01/10/2026", None]
)
def test_parse_money_rejects(text):
    assert parse_money(text) is None


def test_pence_helpers():
    assert to_pence(Decimal("-42.18")) == -4218 and to_pence(Decimal("12.5")) == 1250
    assert pounds(-2005) == "-20.05" and pounds(165000) == "1650.00" and pounds(7) == "0.07"
    assert has_credit_marker("150.00 CR") and not has_credit_marker("CREDIT 150.00")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("01/10/2026", date(2026, 10, 1)),
        ("1 Oct 2026", date(2026, 10, 1)),
        ("01 October 2026", date(2026, 10, 1)),
        ("1st October 2026", date(2026, 10, 1)),
        ("2026-10-01", date(2026, 10, 1)),
        ("30 Sept 2026", date(2026, 9, 30)),
        ("2026-10-01 08:00:05", date(2026, 10, 1)),
        ("01-Oct-2026", date(2026, 10, 1)),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_parse_date_is_day_first_and_rejects_nonsense():
    assert parse_date("03/04/2026") == date(2026, 4, 3)
    assert (
        parse_date("31/02/2026") is None and parse_date("soon") is None and parse_date("") is None
    )


def test_decode_handles_bom_windows_1252_and_utf16():
    assert decode_text(codecs.BOM_UTF8 + "£5\r\n".encode()) == "£5\n"
    assert decode_text("£5".encode("cp1252")) == "£5"
    assert decode_text("£5".encode("utf-16")) == "£5"
