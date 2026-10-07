"""Text, money and dates as UK statements print them. Pure functions."""

from __future__ import annotations

import codecs
import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

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


def has_credit_marker(text: str) -> bool:
    return bool(re.search(r"\bCR\.?\s*$", text.strip(), re.IGNORECASE))


def to_pence(value: Decimal) -> int:
    return int((value * 100).to_integral_value())


def parse_date(text: str | None, formats: Sequence[str] = DATE_FORMATS) -> date | None:
    if not text:
        return None
    cleaned = _ORDINAL.sub(r"\1", " ".join(text.strip().split()))
    cleaned = re.sub(r"\bSept\b", "Sep", cleaned, flags=re.IGNORECASE)
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


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
