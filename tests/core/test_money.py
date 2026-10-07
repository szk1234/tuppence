import time

import pytest

from tuppence.core.errors import InputError
from tuppence.core.money import format_pounds, parse_pounds

# The shared money-text table. The UI's parsePoundsInput is tested against the same table in
# web/src/lib/money.test.ts: keep the two lists identical. None means "rejected".
SHARED_CASES: list[tuple[str, str | None]] = [
    ("1450", "1450.00"),
    ("1,450", "1450.00"),
    ("£1,450", "1450.00"),
    ("12,345.67", "12345.67"),
    ("1,234,567.89", "1234567.89"),
    (" 1,450.5 ", "1450.50"),
    ("£ 7", "7.00"),
    ("0.1", "0.10"),
    ("007.07", "7.07"),
    ("0", "0.00"),
    ("19.99", "19.99"),
    ("999999999.99", "999999999.99"),
    ("1000000000", "1000000000.00"),
    ("1,000,000,000", "1000000000.00"),
    ("12,34", None),
    ("1,2,3", None),
    (",5", None),
    ("5,", None),
    ("12,34.56", None),
    ("£1,45,0", None),
    ("1,45", None),
    ("1,4500", None),
    ("0,450", None),
    ("1 450", None),
    ("1 450.00", None),
    ("12.345", None),
    ("1.", None),
    (".5", None),
    ("1..2", None),
    ("1.2.3", None),
    ("abc", None),
    ("", None),
    ("£", None),
    ("-5", None),
    ("1e3", None),
    ("NaN", None),
    ("١٢٣", None),
    ("1000000000.01", None),
    ("12345678901", None),
    (" " * 28 + "1450", "1450.00"),  # 32 characters: the longest text read as money
    (" " * 29 + "1450", None),  # 33: refused before any pattern runs
    ("0" * 30 + "1.00", None),
]


@pytest.mark.parametrize(("raw", "pounds"), SHARED_CASES)
def test_shared_money_table(raw, pounds):
    if pounds is None:
        with pytest.raises(InputError):
            parse_pounds(raw)
    else:
        assert format_pounds(parse_pounds(raw)) == pounds


@pytest.mark.parametrize("raw", [1450, True, None, 12.3, 1e300, 10**25, b"12"])
def test_only_text_is_money(raw):
    with pytest.raises(InputError):
        parse_pounds(raw)


@pytest.mark.parametrize(
    "raw",
    ["1," * 5000, "1" * 10000 + "x", " " * 10000 + "x", "-" + " " * 10000 + "£" + " " * 10000],
)
def test_adversarial_text_is_refused_fast(raw):
    start = time.perf_counter()
    with pytest.raises(InputError):
        parse_pounds(raw)
    assert time.perf_counter() - start < 0.05


def test_negative_when_allowed():
    assert parse_pounds("-20.05", allow_negative=True) == -2005
    assert parse_pounds("-£1,450.50", allow_negative=True) == -145050
    with pytest.raises(InputError, match="positive"):
        parse_pounds("-20.05")


def test_format():
    assert format_pounds(145000) == "1450.00"
    assert format_pounds(-2005) == "-20.05"
    assert format_pounds(1) == "0.01"


def test_upper_bound_is_inclusive():
    assert parse_pounds("1000000000") == 100_000_000_000
    with pytest.raises(InputError, match="too large"):
        parse_pounds("1000000000.01")
    with pytest.raises(InputError):
        parse_pounds("99999999999999999999999.99")
