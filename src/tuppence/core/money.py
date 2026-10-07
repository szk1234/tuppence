"""Pounds in, pence stored: no floats for money.

The text rule is shared with the UI's `parsePoundsInput` (web/src/lib/money.ts) and both are
tested against the same table of cases:

* an optional "£" and whitespace around the amount are allowed;
* the amount is plain digits ("1450", "1450.5") or digits with commas as thousands separators,
  in groups of three after the first group ("1,450", "12,345.67"); any other comma ("12,34",
  "1,2,3", ",5") and any space inside the number ("1 450") is rejected;
* at most two decimal places, at most £1,000,000,000;
* text longer than 32 characters is refused before any pattern runs.

Only text is accepted: a JSON number for money is refused, so every money field behaves the same.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from tuppence.core.errors import InputError

# Longer text is never an amount: refused before any pattern runs, so no input can be slow.
MAX_TEXT = 32
# One anchored pattern, no nested or overlapping quantifiers (the £, sign and spaces are
# stripped with string operations first).
_SHAPE = re.compile(r"(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,2})?")
MAX_PENCE = 100_000_000_000  # £1,000,000,000
MESSAGE = "Enter an amount like 1450 or 1,450.50."


def parse_pounds(value: str, *, allow_negative: bool = False) -> int:
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise InputError(MESSAGE)
    text = value.strip()
    negative = text.startswith("-")
    if negative:
        text = text[1:].lstrip()
    if text.startswith("£"):
        text = text[1:].lstrip()
    if _SHAPE.fullmatch(text) is None:
        raise InputError(MESSAGE)
    try:
        amount = Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise InputError(MESSAGE) from None
    if negative:
        if not allow_negative:
            raise InputError("Enter a positive amount.")
        amount = -amount
    pence = int((amount * 100).to_integral_value())
    if abs(pence) > MAX_PENCE:
        raise InputError("That amount is too large.")
    return pence


def format_pounds(pence: int) -> str:
    sign = "-" if pence < 0 else ""
    p = abs(pence)
    return f"{sign}{p // 100}.{p % 100:02d}"
