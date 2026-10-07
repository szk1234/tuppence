"""UK postcode district handling (outward code only; never store a full postcode)."""

from __future__ import annotations

import re

from tuppence.core.errors import InputError

_DISTRICT = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
_FULL = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}$")


def normalise_district(value: str) -> str:
    v = value.strip().upper()
    if _FULL.match(v):
        raise InputError("Just the first part of your postcode, please (for example LS6).")
    if not _DISTRICT.match(v):
        raise InputError("That doesn't look like a UK postcode district (for example LS6).")
    return v
