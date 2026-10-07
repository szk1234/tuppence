"""Account and identity details: the one classifier every privacy filter uses.

Text prep withholds a statement line that holds any of these classes, the screenshot path
withholds such a line from an app screenshot, and CSV layout learning hides a heading or a cell
that holds one. Keeping a single list means a spelling caught by one filter is caught by all.

Classes come in two kinds. A *label* names a detail without giving it ("Sort code",
"Account number", "IBAN"); a *value* gives it (12-34-56, ****4242, a holder's name, a postcode).
Lines are withheld for either; a CSV heading row is refused only for a value.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

_I = re.IGNORECASE
_STREET = (
    r"road|rd|street|st|lane|ln|avenue|ave|close|drive|way|gardens|court|place|terrace"
    r"|crescent|square|hill|grove|mews|walk"
)
LABELS: dict[str, re.Pattern[str]] = {
    "account_label": re.compile(
        r"\b(?:account|acct?|a/c)\.?\s*(?:no\.?|num(?:ber)?|name|holders?|type)\b", _I
    ),
    "customer_label": re.compile(
        r"\b(?:customer|membership|roll)\s*(?:no\.?|num(?:ber)?|id)\b", _I
    ),
    "sort_code_label": re.compile(r"\bsort\s*code\b", _I),
    "card_label": re.compile(r"\bcard\s+(?:ending|number|no\.?)\b", _I),
    "bank_code_label": re.compile(r"\biban\b|\bbic\b", _I),
    "holder_label": re.compile(r"^\s*holder\b", _I),
}
VALUES: dict[str, re.Pattern[str]] = {
    # an account, sort code or roll number after its abbreviation: "A/C 12345678",
    # "Acct -71004", "s/c: 123456"
    "account_number": re.compile(
        r"(?<![\w/])(?:account|acct?|a/c|s/c|s\.c\.)\.?\s*:?\s*#?\s*[-−–]?\s*"
        r"\d[\d -]{3,}\d",
        _I,
    ),
    "sort_code": re.compile(r"\b\d{2}-\d{2}-\d{2}\b", _I),
    "card_number": re.compile(r"\b(?:\d[ -]?){12,18}\d\b", _I),
    "card_ending": re.compile(
        r"\bending\s+(?:in\s+)?\d{4}\b|\*{2,}[\s-]*\d{2,4}|(?<![a-z])x{2,}[\s-]*\d{4}\b"
        r"|\b\d{4}[\s-]*\*{2,}",
        _I,
    ),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}\s?[A-Z0-9]{4}(?:\s?\d{4}){2,}(?:\s?[A-Z0-9]{1,4})?\b", _I),
    "postcode": re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", _I),
    "address": re.compile(
        rf"(?<![\d/.-])\d{{1,3}}[a-z]?,?\s+(?:[A-Za-z']+\s+){{1,2}}(?:{_STREET})\b", _I
    ),
    "holder_name": re.compile(
        r"^\s*(?:mr|mrs|ms|miss|mx|dr|prof)\.?\s+[A-Za-z]|^\s*(?:statement|prepared)\s+for\b",
        _I,
    ),
}
LABEL_CLASSES = frozenset(LABELS)

# A sort code spelled with spaces, dots or a long dash: 12 34 56, 12.34.56, 12–34–56. One that
# reads as a real day and month (01.10.26) is taken for a date instead.
_SPELLED_SORT_CODE = re.compile(r"(?<![\d.,:/£$])(\d{2})([ .–—])(\d{2})\2(\d{2})(?![\d,:/]|\.\d)")
_TITLE = re.compile(r"^(?:mr|mrs|ms|miss|mx|dr|prof)\.?\s+", _I)
_NAME_LINE = re.compile(r"^(?:[A-Z][A-Za-z'’-]*)(?:\s+[A-Z][A-Za-z'’-]*){1,3}$")
_DIGIT_RUN = re.compile(r"\d{4,}")
HIDDEN = "<HIDDEN>"


def name_key(text: str) -> str | None:
    """Normalised form of a line that is only a person's name (two to four capitalised
    words, an optional title, no digits), else None."""
    stripped = _TITLE.sub("", text.strip())
    if not _NAME_LINE.fullmatch(stripped) or any(ch.isdigit() for ch in stripped):
        return None
    return " ".join(stripped.casefold().split())


def _words(text: str) -> list[str]:
    return re.sub(r"[.,]", " ", _TITLE.sub("", text.strip())).casefold().split()


@lru_cache(maxsize=64)
def _name_forms(names: tuple[str, ...]) -> frozenset[str]:
    """Each household name as it may be printed: in full, first name and surname, or an
    initial and surname ("A EXAMPLE", "A. Example")."""
    forms: set[str] = set()
    for name in names:
        words = _words(name)
        if len(words) < 2:
            continue
        forms.update(
            {
                " ".join(words),
                f"{words[0]} {words[-1]}",
                f"{words[0][0]} {words[-1]}",
                " ".join([words[0][0], *words[1:]]),
            }
        )
    return frozenset(forms)


def _is_holder(text: str, names: Sequence[str]) -> bool:
    """A line that is only one of the household's names (in any of its printed forms)."""
    if not names or any(ch.isdigit() for ch in text):
        return False
    return " ".join(_words(text)) in _name_forms(tuple(names))


def _spelled_sort_code(text: str) -> bool:
    for match in _SPELLED_SORT_CODE.finditer(text):
        day, month = int(match.group(1)), int(match.group(3))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return True
    return False


def classify(text: str, *, names: Sequence[str] = ()) -> set[str]:
    """The sensitive classes in `text` (empty when there are none). `names` are the
    household's own names: a line that is only one of them is a holder name."""
    found = {name for name, pattern in (*LABELS.items(), *VALUES.items()) if pattern.search(text)}
    if _spelled_sort_code(text):
        found.add("sort_code")
    if _is_holder(text, names):
        found.add("holder_name")
    return found


def is_sensitive(text: str, *, names: Sequence[str] = ()) -> bool:
    return bool(classify(text, names=names))


def holds_details(text: str, *, names: Sequence[str] = ()) -> bool:
    """`text` gives a detail rather than only naming one: a value class, or a label with
    digits beside it ("Sort code 123456")."""
    found = classify(text, names=names)
    return bool(found - LABEL_CLASSES) or (bool(found) and any(ch.isdigit() for ch in text))


def mask(text: str, *, names: Sequence[str] = ()) -> str:
    """`text` as it may be shown to a model: HIDDEN when it holds any sensitive class, else
    with every run of four or more digits replaced by <NUM>."""
    if is_sensitive(text, names=names):
        return HIDDEN
    return _DIGIT_RUN.sub("<NUM>", text)
