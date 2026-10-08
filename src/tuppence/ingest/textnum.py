"""Text, money and dates as UK statements print them. Pure functions."""

from __future__ import annotations

import codecs
import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Literal

from tuppence.core.money import format_pounds

pounds = format_pounds

_SIGN = r"[+\-\u2212\u2013]"  # plus, hyphen, minus sign, en dash
# Each optional piece takes the spaces next to it, so a long gap can't make matching slow.
_MONEY = re.compile(
    r"^(?:(?P<open>\()\s*)?(?:(?P<lead>"
    + _SIGN
    + r")\s*)?(?:(?:GBP|[£$€])\s*)?(?:(?P<lead2>"
    + _SIGN
    + r")\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<dec>\d{1,2}))?(?:\s*(?P<close>\)))?"
    r"(?:\s*(?P<trail>" + _SIGN + r"))?(?:\s*(?P<marker>CR|DR))?\.?$",
    re.IGNORECASE,
)
DATE_FORMATS: tuple[str, ...] = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d %b %y",
    "%d-%b-%y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%Y%m%d",
)
# The longest whole-pounds part a money figure may have, commas included ("1,000,000,000,000"):
# a longer run of digits is a reference or an account number, never money, and reading it as
# one would make a huge number (slow to convert, and larger than any balance).
MAX_FIGURE_CHARS = 20
_ORDINAL = re.compile(r"(\d)(st|nd|rd|th)\b", re.IGNORECASE)


def parse_money(text: str | None) -> Decimal | None:
    """`£1,234.56` → 1234.56. A minus (leading, trailing or −), brackets or DR make it
    negative; CR keeps it positive. Anything that isn't a single money figure → None."""
    if text is None:
        return None
    cleaned = text.strip().replace(" ", " ")
    if not cleaned:
        return None
    match = _MONEY.match(cleaned)
    if not match:
        return None
    if bool(match["open"]) != bool(match["close"]):
        return None
    if len(match["num"]) > MAX_FIGURE_CHARS:  # no statement prints money this long
        return None
    try:
        value = Decimal(match["num"].replace(",", "") + "." + (match["dec"] or "0"))
    except InvalidOperation:
        return None
    signs = [match["lead"], match["lead2"], match["trail"]]
    negative = any(s and s != "+" for s in signs) or bool(match["open"])
    if (match["marker"] or "").upper() == "DR":
        negative = True
    return -value if negative else value


_DIRECTIONS = {"dr": -1, "d": -1, "debit": -1, "cr": 1, "c": 1, "credit": 1}


def direction_of(text: str | None) -> int | None:
    """-1 for a cell that marks money out (DR, D, Debit), +1 for money in (CR, C, Credit),
    else None. A column of these is the only thing that gives an unsigned amount a direction."""
    return _DIRECTIONS.get((text or "").strip().rstrip(".").casefold())


_PRINTED_SIGN = re.compile(r"[-+\u2212\u2013(]|\b(?:CR|DR)\.?\s*$", re.IGNORECASE)


def has_printed_sign(text: str) -> bool:
    """An amount printed with its own direction: a plus or minus, brackets, or a CR/DR mark."""
    return _PRINTED_SIGN.search(text.strip()) is not None


def has_credit_marker(text: str) -> bool:
    return bool(re.search(r"\bCR\.?\s*$", text.strip(), re.IGNORECASE))


def to_pence(value: Decimal) -> int:
    return int((value * 100).to_integral_value())


# Numeric dates, read without trying every format in turn: "01/10/2026", "1/10/26",
# "01-10-2026", "01.10.2026", "2026-10-01". Exactly what `DATE_FORMATS` accepts for them (a
# date-shaped figure such as a sort code costs one look, not sixteen failed formats).
_NUMERIC = re.compile(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{4}|\d{2})|(\d{4})-(\d{1,2})-(\d{1,2})")
_NUMERIC_FORMATS = {"/4", "/2", "-4", ".4"}  # %d/%m/%Y, %d/%m/%y, %d-%m-%Y, %d.%m.%Y


def _numeric_date(cleaned: str) -> date | None | Literal[False]:
    """With `DATE_FORMATS`: the date for a numeric spelling, None when it is one that isn't a
    date, or False when it isn't numeric (the formats are tried one by one then)."""
    match = _NUMERIC.fullmatch(cleaned)
    if match is None:
        return False
    if match.group(5) is not None:
        year, month, day = int(match.group(5)), int(match.group(6)), int(match.group(7))
    else:
        if f"{match.group(2)}{len(match.group(4))}" not in _NUMERIC_FORMATS:
            return None  # "12-34-56": no format reads it
        day, month, year = int(match.group(1)), int(match.group(3)), int(match.group(4))
        if len(match.group(4)) == 2:  # as strptime reads %y
            year += 1900 if year >= 69 else 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


@lru_cache(maxsize=8192)
def _parse_date(cleaned: str, formats: tuple[str, ...]) -> date | None:
    quick = _numeric_date(cleaned) if formats == DATE_FORMATS else False
    if quick is not False:
        return quick
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def parse_date(text: str | None, formats: Sequence[str] = DATE_FORMATS) -> date | None:
    if not text:
        return None
    cleaned = _ORDINAL.sub(r"\1", " ".join(text.strip().split()))
    cleaned = re.sub(r"\bSept\b", "Sep", cleaned, flags=re.IGNORECASE)
    if len(cleaned) > 64:  # no date is this long
        return None
    return _parse_date(cleaned, tuple(formats))


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM), UTF-16 with BOM, else Windows-1252 (Excel's £)."""
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        text = data.decode("utf-16")
    else:
        if data.startswith(codecs.BOM_UTF8):
            data = data[len(codecs.BOM_UTF8) :]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")
