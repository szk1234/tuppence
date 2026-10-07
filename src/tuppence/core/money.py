"""Pounds in, pence stored: no floats for money."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from tuppence.core.errors import InputError

_CLEAN = re.compile(r"[£,\s]")
_SHAPE = re.compile(r"^-?\d+(\.\d{1,2})?$")
MESSAGE = "Enter an amount like 1450 or 1,450.50."


def parse_pounds(value: str | int | float | Decimal, *, allow_negative: bool = False) -> int:
    if isinstance(value, bool):
        raise InputError(MESSAGE)
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        text = f"{value:.2f}" if round(value, 2) == value else repr(value)
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
    return int((amount * 100).to_integral_value())


def format_pounds(pence: int) -> str:
    sign = "-" if pence < 0 else ""
    p = abs(pence)
    return f"{sign}{p // 100}.{p % 100:02d}"
