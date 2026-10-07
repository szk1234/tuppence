"""Pounds in, pence stored: no floats for money."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from tuppence.core.errors import InputError

_CLEAN = re.compile(r"[£,\s]")
_SHAPE = re.compile(r"^-?[0-9]+(\.[0-9]{1,2})?$")
MAX_PENCE = 100_000_000_000  # £1,000,000,000
MESSAGE = "Enter an amount like 1450 or 1,450.50."


def parse_pounds(value: str | int | Decimal, *, allow_negative: bool = False) -> int:
    if isinstance(value, bool):
        raise InputError(MESSAGE)
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, str):
        text = _CLEAN.sub("", value)
    else:
        raise InputError(MESSAGE)
    if not _SHAPE.match(text):
        raise InputError(MESSAGE)
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise InputError(MESSAGE) from None
    if amount < 0 and not allow_negative:
        raise InputError("Enter a positive amount.")
    pence = int((amount * 100).to_integral_value())
    if abs(pence) > MAX_PENCE:
        raise InputError("That amount is too large.")
    return pence


def format_pounds(pence: int) -> str:
    sign = "-" if pence < 0 else ""
    p = abs(pence)
    return f"{sign}{p // 100}.{p % 100:02d}"
