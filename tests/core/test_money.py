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
        (12.3, 1230),
    ],
)
def test_parse(raw, pence):
    assert parse_pounds(raw) == pence


@pytest.mark.parametrize("raw", ["12.345", "abc", "", "1.2.3", "£", "-5", True, None])
def test_parse_rejects(raw):
    with pytest.raises(InputError):
        parse_pounds(raw)


def test_negative_when_allowed():
    assert parse_pounds("-20.05", allow_negative=True) == -2005


def test_format():
    assert format_pounds(145000) == "1450.00"
    assert format_pounds(-2005) == "-20.05"
    assert format_pounds(1) == "0.01"
