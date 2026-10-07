"""Account and identity details: the one classifier every privacy filter uses.

Text prep withholds a statement line that holds any of these classes, the screenshot path
withholds such a line from an app screenshot, and CSV layout learning hides a heading or a cell
that holds one. Keeping a single list means a spelling caught by one filter is caught by all.

Classes come in two kinds. A *label* names a detail without giving it ("Sort code",
"Account number", "IBAN"); a *value* gives it (12-34-56, ****4242, a holder's name, a postcode,
a balance line such as "Available balance £1,184.56"). Lines are withheld for either; a CSV
heading row is refused only for a value.
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
# Balance lines. Every optional piece takes the spaces after it, so no run of spaces can be split
# more than one way (a long gap must not make matching slow).
_BALANCE_WORD = (
    r"(?:your|available|current|account|cleared|running|opening|closing|new|previous|starting"
    r"|ending|start|end|statement|outstanding|total|spending|actual)"
)
_BALANCE_LABEL = (
    rf"(?:{_BALANCE_WORD}\s+){{0,2}}"
    r"(?:balance(?:\s+(?:after|owing|owed|outstanding|remaining|available))?"
    r"|available(?:\s+(?:to\s+spend|credit|funds))?"
    r"|(?:arranged\s+)?overdraft\s+limit|arranged\s+overdraft|credit\s+limit)"
)
_SIGN = r"[-+\u2212\u2013]"
_FIGURE = (
    rf"(?:\(\s*)?(?:{_SIGN}\s*)?(?:(?:[£$€]|GBP\b)\s*)?(?:{_SIGN}\s*)?"
    r"(?:\d{1,3}(?:[,\u00a0 ]\d{3})+|\d+)(?:\.\d{2})?"
    r"(?:\s*\))?(?:\s*GBP\b)?(?:\s*[-\u2212](?!\d))?"
    r"(?:\s*(?:CR|DR|O/D|OD|D|in\s+credit|overdrawn)\b)?(?:\.|\s*\*)?"
)
_BALANCE_ITEM = rf"(?:{_BALANCE_LABEL}\s*(?::\s*)?{_FIGURE}|{_FIGURE}\s+{_BALANCE_LABEL})"
_BALANCE_LINE = re.compile(
    rf"^\s*{_BALANCE_ITEM}(?:\s*(?:[|·•,;/]\s*)?{_BALANCE_ITEM}){{0,2}}\s*$", re.IGNORECASE
)
_LABEL_ONLY = re.compile(rf"^\s*{_BALANCE_LABEL}\s*(?::\s*)?$", re.IGNORECASE)
_FIGURE_ONLY = re.compile(rf"^\s*{_FIGURE}\s*$", re.IGNORECASE)
_MONEY_SHAPE = re.compile(r"[£$€]|GBP|\.\d{2}", re.IGNORECASE)

LABELS: dict[str, re.Pattern[str]] = {
    "account_label": re.compile(
        r"\b(?:account|acct?|a/c)\.?\s*(?:no\.?|num(?:ber)?|name|holders?|type)\b", _I
    ),
    "customer_label": re.compile(
        r"\b(?:customer|membership|roll)\s*(?:no\.?|num(?:ber)?|id|ref(?:erence)?)\b", _I
    ),
    "sort_code_label": re.compile(r"\bsort\s*code\b", _I),
    "card_label": re.compile(r"\bcard\s+(?:ending|number|no\.?)\b", _I),
    "bank_code_label": re.compile(r"\biban\b|\bbic\b", _I),
    "holder_label": re.compile(r"^\s*(?:holder\b|name\s*:|joint\s+account\s*:)", _I),
}
VALUES: dict[str, re.Pattern[str]] = {
    # an account, sort code or roll number after its abbreviation: "A/C 12345678",
    # "Acct -71004", "s/c: 123456"
    "account_number": re.compile(
        r"(?<![\w/])(?:account|acct?|ac|a/c|s/c|s\.c\.|sc)\.?\s*(?::\s*)?(?:#\s*)?(?:[-−–]\s*)?"
        r"\d[\d -]{3,}\d",
        _I,
    ),
    "sort_code": re.compile(r"\b\d{2}-\d{2}-\d{2}\b", _I),
    # a full card number; laid-out text may leave a wide gap between its groups of four
    "card_number": re.compile(r"\b(?:\d[ -]?){12,18}\d\b|\b\d{4}(?:[ -]{1,3}\d{4}){3}\b", _I),
    "card_ending": re.compile(
        r"\bending\s+(?:in\s+)?\d{3,4}\b|\*{2,}[\s-]*\d{2,4}|(?<![a-z])x{2,}[\s-]*\d{4}\b"
        r"|\b\d{4}[\s-]*\*{2,}"
        # the last digits behind bullets, dots or an ellipsis: "•••• 4242", "...4242", "…4242"
        r"|[•●·∙◦∗]{2,}[\s-]*\d{2,4}(?![.,]?\d)|(?:…|\.{3,})\s*\d{4}(?![.,]?\d)",
        _I,
    ),
    "swift_code": re.compile(
        r"\b(?i:swift)(?:\s+(?i:code))?\s*(?::\s*)?[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b"
    ),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}\s?[A-Z0-9]{4}(?:\s?\d{4}){2,}(?:\s?[A-Z0-9]{1,4})?\b", _I),
    "postcode": re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", _I),
    "address": re.compile(
        rf"(?<![\d/.-])\d{{1,3}}[a-z]?,?\s+(?:[A-Za-z']+\s+){{1,2}}(?:{_STREET})\b", _I
    ),
    # A title and a name. "Dr" before column vocabulary ("Dr Amount") is a debit column.
    "holder_name": re.compile(
        r"^\s*(?:mr|mrs|ms|miss|mx|dr|prof)\.?\s+(?:&\s*(?:mr|mrs|ms|miss|mx|dr)\.?\s+)?"
        r"(?!(?:amount|amt|total|value|balance|debit|credit|cr|dr|ref)\b)[A-Za-z]"
        r"|^\s*(?:statement|prepared)\s+for\b",
        _I,
    ),
    # A balance line: balance or limit labels and their figures, nothing else (no date, no
    # description), so it can't be a transaction.
    "balance_line": _BALANCE_LINE,
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
    """Each household name as it may be printed: in full, first name and surname, an initial
    and surname ("A EXAMPLE", "A. Example"), or surname first ("Example, Alex", "EXAMPLE A")."""
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
                f"{words[-1]} {words[0]}",
                f"{words[-1]} {words[0][0]}",
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


def is_balance_line(text: str) -> bool:
    """A line that is only balances or limits and their figures ("Balance £1,234.56",
    "£1,184.56 available", "Balance £1,234.56 Available £1,184.56")."""
    return _BALANCE_LINE.search(text) is not None


def is_figure_line(text: str) -> bool:
    """A line that is only a money figure ("£1,184.56", "1,000.00 CR")."""
    return _FIGURE_ONLY.search(text) is not None and _MONEY_SHAPE.search(text) is not None


def balance_lines(texts: Sequence[str]) -> set[int]:
    """Indexes of the lines that show a balance: balance lines, and a balance label printed on
    a line of its own together with the figure-only line below it (or, when there is none
    below, above it), as apps lay out "Available balance" under "£1,184.56"."""
    found = {i for i, text in enumerate(texts) if is_balance_line(text)}
    for i, text in enumerate(texts):
        if not _LABEL_ONLY.search(text):
            continue
        if i + 1 < len(texts) and is_figure_line(texts[i + 1]):
            found.update((i, i + 1))
        elif i > 0 and is_figure_line(texts[i - 1]) and i - 1 not in found:
            found.update((i - 1, i))
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
