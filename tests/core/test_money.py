from decimal import Decimal

import pytest

from tuppence.core.errors import InputError
from tuppence.core.money import format_pounds, parse_pounds


@pytest.mark.parametrize(
    "raw,pence",
    [
        ("£1,450", 145000),
        ("1450.5", 145050),
        (1450, 145000),
        ("0.01", 1),
        (Decimal("12.30"), 1230),
        (" £ 7 ", 700),
    ],
)
def test_parse(raw, pence):
    assert parse_pounds(raw) == pence


@pytest.mark.parametrize(
    "raw",
    [
        "12.345",
        "abc",
        "",
        "1.2.3",
        "£",
        "-5",
        True,
        None,
        12.3,
        1e300,
        "١٢٣",
        "NaN",
        "1e3",
        "99999999999999999999999.99",
        10**25,
        "1000000000.01",
    ],
)
def test_parse_rejects(raw):
    with pytest.raises(InputError):
        parse_pounds(raw)


def test_negative_when_allowed():
    assert parse_pounds("-20.05", allow_negative=True) == -2005


def test_format():
    assert format_pounds(145000) == "1450.00"
    assert format_pounds(-2005) == "-20.05"
    assert format_pounds(1) == "0.01"


def test_upper_bound_is_inclusive():
    assert parse_pounds("1000000000") == 100_000_000_000
    with pytest.raises(InputError, match="too large"):
        parse_pounds("1000000000.01")
