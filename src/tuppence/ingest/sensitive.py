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
import unicodedata
from collections.abc import Sequence
from datetime import date
from functools import lru_cache

from tuppence.ingest.models import MaskedLine
from tuppence.ingest.textnum import parse_date

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
    r"|(?:arranged\s+)?overdraft\s+limit|arranged\s+overdraft|credit\s+limit"
    # what a card statement asks for, and when (I1)
    r"|minimum\s+(?:payment|amount)(?:\s+due)?|(?:payment|amount)\s+due)"
)
# A date printed between a label and its figure ("Balance on 31/10/2026 £1.00"), or as the value
# of a dated label ("Statement date 05/11/2026", "Payment due by 20/11/2026").
_DATE_WORDS = (
    r"(?:\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?(?:\s+\d{2,4}\b)?"
    r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\d{4}-\d{2}-\d{2}\b)"
)
_ON_DATE = rf"(?:(?:on|at|as\s+(?:at|of)|of|by|for)\s+)?{_DATE_WORDS}"
_DATED_LABEL = r"(?:statement\s+date|(?:payment\s+)?due\s+date|date\s+due|payment\s+due)"
_SIGN = r"[-+\u2212\u2013]"
_FIGURE = (
    rf"(?:\(\s*)?(?:{_SIGN}\s*)?(?:(?:[£$€]|GBP\b)\s*)?(?:{_SIGN}\s*)?"
    r"(?>\d{1,3}(?:[,\u00a0 ]\d{3})+|\d+)(?:\.\d{2})?"
    r"(?:\s*\))?(?:\s*GBP\b)?(?:\s*[-\u2212](?!\d))?"
    r"(?:\s*(?:CR|DR|O/D|OD|D|in\s+credit|overdrawn)\b)?(?:\.|\s*\*)?"
)
_BALANCE_ITEM = (
    rf"(?:{_BALANCE_LABEL}(?:\s+{_ON_DATE})?\s*(?::\s*)?{_FIGURE}"
    rf"|{_FIGURE}\s+{_BALANCE_LABEL}"
    rf"|{_DATED_LABEL}\s*(?::\s*)?{_ON_DATE})"
)
_BALANCE_LINE = re.compile(
    rf"^\s*{_BALANCE_ITEM}(?:\s*(?:[|·•,;/:]\s*)?{_BALANCE_ITEM}){{0,2}}\s*$", re.IGNORECASE
)
# A summary an app prints under its own figure, beside the balance ("£20.00" over "Spent today").
_SUMMARY_LABEL = r"(?:spent|spending)\s+(?:today|this\s+(?:week|month))"
_LABEL_ONLY = re.compile(rf"^\s*(?:{_BALANCE_LABEL}|{_SUMMARY_LABEL})\s*(?::\s*)?$", re.IGNORECASE)
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
    "holder_label": re.compile(r"^\s*holder\b|\bname\s*:|\bjoint\s+account\s*:", _I),
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
        r"\bending\s+(?:in\s+)?\d{3,4}\b|(?<!\*)\*{2,}[\s-]*\d{2,4}|(?<![a-z])x{2,}[\s-]*\d{4}\b"
        # the short forms (R-M3-23 (a)): "ENDING4242", "*4242", "X4242"
        r"|\bending(?:\s*in)?\s*\d{4}(?!\d)|(?<![\w*])\*\s?\d{4}(?!\d)|(?<![a-z0-9])x\s?\d{4}(?!\d)"
        r"|\b\d{4}[\s-]*(?:[*•●·∙◦∗]{2,}|[xX]{4})"
        # the last digits behind bullets, dots or an ellipsis: "•••• 4242", "...4242", "…4242"
        # (a mask run is tried from its first character only, so a long run stays fast)
        r"|(?<![•●·∙◦∗])[•●·∙◦∗]{2,}[\s-]*\d{2,4}(?![.,]?\d)"
        r"|(?<![.…])(?:…|\.{3,})\s*\d{4}(?![.,]?\d)",
        _I,
    ),
    "swift_code": re.compile(
        r"\b(?i:swift)(?:\s+(?i:code))?\s*(?::\s*)?[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b"
    ),
    "iban": re.compile(
        r"(?:\b|(?<=iban))[A-Z]{2}\d{2}\s?[A-Z0-9]{4}(?:\s?\d{4}){2,}(?:\s?[A-Z0-9]{1,4})?\b", _I
    ),
    "postcode": re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", _I),
    # a house number and street, or a flat ("Flat 3", "Apartment 12", "Apt 4B")
    "address": re.compile(
        rf"(?<![\d/.-])\d{{1,3}}[a-z]?,?\s+(?:[A-Za-z']+\s+){{1,2}}(?:{_STREET})\b"
        r"|\b(?:flat|apartment|apt|unit|suite)\.?\s+\d{1,4}[a-z]?\b(?![.,]\d)",
        _I,
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

# A sort code spelled with spaces, dots or dashes, the same or mixed: 12 34 56, 12.34.56,
# 12-34 56. Printed with the same space or dot twice and reading as a real day and month
# (01.10.26), it is taken for a date instead.
_SPELLED_SORT_CODE = re.compile(
    r"(?<![\d.,:/£$])(\d{2})([ .\-–—·_])(\d{2})([ .\-–—·_])(\d{2})(?![\d,:/]|\.\d)"
)
_TITLE = re.compile(r"^(?:mr|mrs|ms|miss|mx|dr|prof)\.?\s+", _I)
_NAME_LINE = re.compile(r"^(?:[A-Z][A-Za-z'’-]*)(?:\s+[A-Z][A-Za-z'’-]*){1,3}$")
_DIGIT_RUN = re.compile(r"\d{4,}")
HIDDEN = "<HIDDEN>"


# --- one normalisation for every filter and for what is sent ----------------------------------
#
# A statement line is kept, classified, masked and sent as one string: `normalise` of the line
# as printed. Compatibility forms are folded (NFKC: full-width and styled digits and letters
# become plain ones, non-breaking and narrow spaces become spaces), invisible format characters
# (zero-width spaces, soft hyphens) and stray combining or enclosing marks (a keycap) are
# dropped, curly apostrophes and Unicode dashes become ' and -, and every run of whitespace is
# one space. Patterns then read
# that string with accents folded character by character (é is read as e), which keeps every
# position, like reading it case-insensitively.

_UNIFY = str.maketrans(
    {
        **{c: "'" for c in "\u2018\u2019\u02bc\u201b"},
        **{c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63"},
    }
)


def normalise(text: str) -> str:
    """The line as Tuppence keeps, reads and sends it (see above)."""
    if not text.isascii():
        text = unicodedata.normalize("NFKC", text)
        text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Mn", "Me"))
        text = text.translate(_UNIFY)
    return " ".join(text.split())


view = normalise  # the line as every filter reads it


@lru_cache(maxsize=4096)
def _base(ch: str) -> str:
    decomposed = unicodedata.normalize("NFKD", ch)
    if len(decomposed) > 1 and all(unicodedata.combining(c) for c in decomposed[1:]):
        return decomposed[0]
    return ch


def _letters(text: str) -> str:
    """`text` with accents folded one character for one (é → e), so positions are kept."""
    return text if text.isascii() else "".join(_base(ch) for ch in text)


def _seen(text: str) -> tuple[str, str]:
    """(the normalised line, the same with accents folded): what patterns read."""
    normal = normalise(text)
    return normal, _letters(normal)


def name_key(text: str) -> str | None:
    """Normalised form of a line that is only a person's name (two to four capitalised
    words, an optional title, no digits), else None."""
    stripped = _TITLE.sub("", _seen(text)[1])
    if not _NAME_LINE.fullmatch(stripped) or any(ch.isdigit() for ch in stripped):
        return None
    return " ".join(stripped.casefold().split())


def _words(text: str) -> list[str]:
    return re.sub(r"[.,]", " ", _TITLE.sub("", text.strip())).casefold().split()


@lru_cache(maxsize=64)
def _name_forms(names: tuple[str, ...]) -> frozenset[str]:
    """Each name as it may be printed: in full, first name and surname, initials and surname
    ("J SMITH", "J. Smith", "J P SMITH"), surname first ("Smith, John", "SMITH J"), and glued
    into one word ("JSMITH", "SMITHJ", "JOHNSMITH")."""
    forms: set[str] = set()
    for name in names:
        words = _words(_seen(name)[1])
        if len(words) < 2:
            continue
        first, last = words[0], words[-1]
        initials = [w[0] for w in words[:-1]]
        forms.update(
            {
                " ".join(words),
                f"{first} {last}",
                f"{first[0]} {last}",
                " ".join([*initials, last]),
                " ".join([first[0], *words[1:]]),
                f"{last} {first}",
                f"{last} {first[0]}",
                f"{last} {' '.join(initials)}",
            }
        )
        if len(last) >= 3:  # glued: "JSMITH", "SMITHJ", "JOHNSMITH"
            forms.update({f"{first[0]}{last}", f"{last}{first[0]}", f"{first}{last}"})
    return frozenset(forms)


@lru_cache(maxsize=64)
def _name_pattern(names: tuple[str, ...]) -> re.Pattern[str] | None:
    """Any printed form of a name, anywhere in a line, in any case."""
    forms = sorted(_name_forms(names), key=len, reverse=True)
    if not forms:
        return None
    # words joined by spaces or punctuation ("ALEX-EXAMPLE", "EXAMPLE/ALEX", "ALEX_EXAMPLE"),
    # and a name may touch digits ("ALEX EXAMPLE123")
    alternatives = (r"[\s.,_/\-]+".join(re.escape(w) for w in form.split()) for form in forms)
    return re.compile(r"(?<![A-Za-z'])(?:" + "|".join(alternatives) + r")(?![A-Za-z'])", _I)


def _is_holder(text: str, names: Sequence[str]) -> bool:
    """A line that is only one of the household's names (in any of its printed forms)."""
    seen = _seen(text)[1]
    if not names or any(ch.isdigit() for ch in seen):
        return False
    return " ".join(_words(seen)) in _name_forms(tuple(names))


def _spelled_sort_codes(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _SPELLED_SORT_CODE.finditer(text):
        day, month = int(match.group(1)), int(match.group(3))
        same = match.group(2) == match.group(4)
        if not (same and match.group(2) in " ." and 1 <= day <= 31 and 1 <= month <= 12):
            spans.append(match.span())
    return spans


def _classify_seen(seen: str, names: Sequence[str]) -> set[str]:
    found = {name for name, pattern in (*LABELS.items(), *VALUES.items()) if pattern.search(seen)}
    if _spelled_sort_codes(seen):
        found.add("sort_code")
    found |= {name for name, _, _ in _number_matches(seen)}
    if names and (pattern := _name_pattern(tuple(names))) is not None and pattern.search(seen):
        found.add("holder_name")
    return found


def classify(text: str, *, names: Sequence[str] = ()) -> set[str]:
    """The sensitive classes in `normalise(text)` (empty when there are none). `names` are the
    household's names (and the holders printed on the statement): one of them anywhere in the
    line, in any printed form and any case, is a holder name."""
    return _classify_seen(_seen(text)[1], names)


def is_balance_line(text: str) -> bool:
    """A line that is only balances or limits and their figures ("Balance £1,234.56",
    "£1,184.56 available", "Balance £1,234.56 Available £1,184.56")."""
    return _BALANCE_LINE.search(_seen(text)[1]) is not None


def is_figure_line(text: str) -> bool:
    """A line that is only a money figure ("£1,184.56", "1,000.00 CR")."""
    return _FIGURE_ONLY.search(text) is not None and _MONEY_SHAPE.search(text) is not None


def is_balance_label(text: str) -> bool:
    """A line that is only a balance label ("Available balance", "Balance:") or a summary an
    app prints beside it ("Spent today"): its figure is on the line above or below."""
    return _LABEL_ONLY.search(text) is not None


def is_sensitive(text: str, *, names: Sequence[str] = ()) -> bool:
    return bool(classify(text, names=names))


def holds_details(text: str, *, names: Sequence[str] = ()) -> bool:
    """`text` gives a detail rather than only naming one: a value class, or a label with
    digits beside it ("Sort code 123456")."""
    found = classify(text, names=names)
    return bool(found - LABEL_CLASSES) or (bool(found) and any(ch.isdigit() for ch in text))


def mask(text: str, *, names: Sequence[str] = ()) -> str:
    """`text` as it may be shown to a model (a CSV heading), from `prepare_outbound`: HIDDEN
    when it holds any detail, else with every run of four or more digits replaced by <NUM>."""
    out = prepare_outbound(text, names=names)
    if out is None or out.hidden or classify(out.text, names=names):  # a label hides too
        return HIDDEN
    return _DIGIT_RUN.sub("<NUM>", out.text)


# --- masking details inside a transaction line (I2) -------------------------------------------
#
# Masking reads `normalise(text)` exactly as `classify` does, finds every detail with the same
# patterns, and replaces it in that same string, which is the string sent. A detail `classify`
# sees is a detail `mask_line` hides.

# Never masked: an amount (with pence, or after a currency sign) and a real date.
_KEEP_MONEY = re.compile(
    r"(?<![\w.])(?:(?<!\d,)|(?!\d{3},))[-+−]?[£$€]?\d{1,3}(?:,\d{3})*\.\d{2}(?!\d|\.\d)"
    r"|(?<![\w.])[-+−]?[£$€]\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?(?![\d,])"
)
_KEEP_DATE = re.compile(
    r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"\.?(?:\s+\d{4}\b)?",
    _I,
)
_MASK_CHARS = "*•●·∙◦∗…"
_NAME_WORD = re.compile(r"[A-Za-z&'.\-]+")
_PLACEHOLDER = re.compile(r"\[hidden-[a-z]+\]")
# Labels whose value is a name or a description, not a number: everything after them goes.
_NAME_LABEL = re.compile(r"name|holder|type|joint", _I)


def _value_token(token: str) -> bool:
    """A token that gives a value: letters, digits, mask characters, dots, dashes or slashes,
    with at least one digit or mask character ("12345678", "12-34-56", "****4242")."""
    return all(ch.isalnum() or ch in _MASK_CHARS or ch in "./-" for ch in token) and any(
        ch.isdigit() or ch in _MASK_CHARS for ch in token
    )


def _plausible(day: date | None) -> bool:
    """A date a statement may print: not "24.04.3320", which is digits that happen to parse."""
    return day is not None and 1950 <= day.year <= 2099


def _kept(text: str) -> list[bool]:
    keep = [False] * len(text)
    for match in _KEEP_MONEY.finditer(text):
        keep[match.start() : match.end()] = [True] * (match.end() - match.start())
    for match in _KEEP_DATE.finditer(text):
        value = match.group(0)
        # dd-mm-yy or dd.mm.yy may be a sort code, and "11-33/66" is no date: only a numeric
        # date that reads as one is kept
        numeric = not re.search(r"[A-Za-z]", value)
        if numeric and not _plausible(parse_date(value)):
            continue
        keep[match.start() : match.end()] = [True] * (match.end() - match.start())
    return keep


def _tokens_after(text: str, start: int, keep: Sequence[bool], name_words: bool) -> int:
    """Where the value after a label (or a title) ends: the run of tokens that hold a digit or a
    mask character (or, for a name, words without digits), stopping at an amount or a date."""
    i, end = start, start
    while i < len(text):
        while i < len(text) and (text[i].isspace() or text[i] in ":#."):
            i += 1
        if i >= len(text) or keep[i]:
            break
        j = i
        while j < len(text) and not text[j].isspace():
            j += 1
        token = text[i:j]
        fits = _NAME_WORD.fullmatch(token) if name_words else _value_token(token)
        if not fits or any(keep[i:j]):
            break
        end = i = j
    return end


# --- long numbers (R-M3-23 (a)) -----------------------------------------------------------------
#
# Inside a line that is sent, every run of six or more digits that isn't an amount or a date is
# masked: an account, card, roll or reference number, or a sort code and account number
# together, however they are joined (one space, hyphen, slash, dot, middle dot or underscore
# between groups) and whatever is glued to them ("ACCNO87654321", "J SMITH20-11-33").
#
# Left alone: a well-formed amount (one point, two decimals, comma grouping only: 12.30,
# 1234.56, 1,234.56; or whole pounds after £), a date a pattern reads (02/10/2026, 05.10.2026),
# and a date printed with spaces or dots ("05 10 26", "05.10.26") only where it is the line's
# own date, at its start, with no number of four or more digits joined after it. Anywhere else
# such a group may be a sort code ("TO 20.11.33 87.65.43.21").
#
# A scan may read a digit as a letter (0 as O, 5 as S, 1 as l, I or |, 8 as B, 2 as Z). Inside a
# token that is at least half digits those letters are read as digits for finding numbers, so
# "2O-11-33 876S4321" is masked whole; a word ("BOOTS", "SO", "ZARA") is never read so.

LONG_RUN = 6
_RUN_JOINS = frozenset(" -/.·_")
_DAY_NAME = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s+"
_LEADING_DATE = re.compile(
    rf"^\s*(?:{_DAY_NAME})?(\d{{1,2}})([ .])(\d{{1,2}})\2(\d{{4}}|\d{{2}})(?![\d,:/]|\.\d)",
    re.IGNORECASE,
)
_WELL_FORMED = re.compile(r"(?<![\d,])(?<!\d\.)(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?!\d|[.,]\d)")
_LOOKALIKES = frozenset("OoSslI|BZz")
_AS_DIGITS = str.maketrans("OoSslI|BZz", "0055111822")
_TOKEN = re.compile(r"[A-Za-z0-9|]+")
_LETTERS = re.compile(r"[A-Za-z|]+")


def _read_token(token: str) -> str:
    if not any(ch.isdigit() for ch in token):
        return token  # a word stays a word
    out = list(token)
    words = [
        m.span()
        for m in _LETTERS.finditer(token)
        if len(m.group(0)) >= 2 and any(ch not in _LOOKALIKES for ch in m.group(0))
    ]
    at = 0
    for start, end in [*words, (len(token), len(token))]:
        piece = token[at:start]
        if piece and 2 * sum(ch.isdigit() for ch in piece) >= len(piece):
            out[at:start] = piece.translate(_AS_DIGITS)
        at = end
    return "".join(out)


# A look-alike next to a digit: without one, no reading changes anything.
_NEXT_TO_DIGIT = re.compile(r"\d[OoSslI|BZz]|[OoSslI|BZz]\d")


@lru_cache(maxsize=16)
def _digits_read(seen: str) -> str:
    """`seen` with the letters a scan reads for digits read as digits, inside mostly-digit
    pieces of a token only (see above). Every position is kept."""
    if _NEXT_TO_DIGIT.search(seen) is None:
        return seen
    return _TOKEN.sub(lambda m: _read_token(m.group(0)), seen)


def _long_runs(seen: str) -> list[tuple[int, int]]:
    """Spans of the runs of six or more digits in `seen` (see above), read with look-alikes as
    digits. One pass over the line."""
    view = _digits_read(seen)
    keep = _kept(view)
    size = len(view)
    part = [ch.isdigit() and not keep[k] for k, ch in enumerate(view)]
    for match in _WELL_FORMED.finditer(view):
        for k in range(match.start(), match.end()):
            part[k] = False
    lead = _LEADING_DATE.match(view)
    if lead and 1 <= int(lead.group(1)) <= 31 and 1 <= int(lead.group(3)) <= 12:
        after = lead.end()
        k, digits_after = after, 0
        while k + 1 < size and view[k] in _RUN_JOINS and part[k + 1]:  # a number joined after
            k += 1
            while k < size and part[k]:
                digits_after += 1
                k += 1
        if digits_after < 4:  # the line's own date, not a sort code before an account number
            for k in range(lead.start(1), after):
                part[k] = False
    spans: list[tuple[int, int]] = []
    k = 0
    while k < size:
        if not part[k]:
            k += 1
            continue
        start = end = k
        while end < size and (
            part[end] or (view[end] in _RUN_JOINS and end + 1 < size and part[end + 1])
        ):
            end += 1
        k = end
        if sum(1 for j in range(start, end) if part[j]) < LONG_RUN:
            continue
        before = start - 1
        while before >= 0 and view[before].isspace():
            before -= 1
        if before >= 0 and view[before] in "£$€":
            continue  # whole pounds after a currency sign
        spans.append((start, end))
    return spans


_NUMBER_CLASSES = ("account_number", "sort_code", "card_number", "card_ending")


def _number_matches(seen: str) -> list[tuple[str, int, int]]:
    """The number classes found once look-alikes are read as digits (when that changes the
    line): a sort code or card number a scan misread is still one."""
    view = _digits_read(seen)
    if view == seen:
        return []
    found = [
        (name, m.start(), m.end()) for name in _NUMBER_CLASSES for m in VALUES[name].finditer(view)
    ]
    found += [("sort_code", start, end) for start, end in _spelled_sort_codes(view)]
    return found


class _Unmaskable(Exception):
    """A detail overlaps an amount or a date in a way that can't be split safely."""


def _safe(text: str, start: int, end: int, keep: Sequence[bool]) -> bool:
    """A detail may run on into the amount after it (a card number's pattern takes the pounds
    of "41234567 10.00"). It may not start inside an amount or a date, nor have anything of its
    own after one: then which characters are the detail can't be told ("12 Mar Road", where
    "12 Mar" reads as a date; "12.34.56", where "12.34" reads as an amount)."""
    if start >= end:
        return True
    if keep[start]:
        return False
    seen = False
    for k in range(start, end):
        if keep[k]:
            seen = True
        elif seen and not text[k].isspace():
            return False
    return True


def _detail_spans(seen: str, keep: Sequence[bool], names: Sequence[str]) -> list[tuple[int, int]]:
    """Every detail in the view `seen`, found with `classify`'s own patterns."""
    spans: list[tuple[int, int]] = []

    def add(start: int, end: int) -> None:
        if not _safe(seen, start, end, keep):
            raise _Unmaskable
        spans.append((start, end))

    for name, pattern in VALUES.items():
        if name == "balance_line":
            continue
        for match in pattern.finditer(seen):
            start, end = match.span()
            if name == "card_number":  # its pattern may take the year of a date before it
                while start < end and (keep[start] or seen[start].isspace()):
                    start += 1
            if name in ("account_number", "card_ending"):  # keep the label ("A/C", "ending")
                start = next(
                    (k for k in range(start, end) if seen[k].isdigit() or seen[k] in _MASK_CHARS
                     or seen[k : k + 3] == "..."),
                    start,
                )  # fmt: skip
            elif name == "holder_name":  # the title and the name after it
                end = _tokens_after(seen, match.start(), keep, name_words=True)
            add(start, end)
    for start, end in _spelled_sort_codes(seen):
        add(start, end)
    for name, start, end in _number_matches(seen):  # a scan's misread digits
        while start < end and (keep[start] or seen[start].isspace()):
            start += 1
        if name in ("account_number", "card_ending"):  # keep the label ("A/C", "ending")
            start = next((k for k in range(start, end) if seen[k] in _LOOKALIKES
                          or seen[k].isdigit() or seen[k] in _MASK_CHARS), start)  # fmt: skip
        add(start, end)
    if names and (pattern := _name_pattern(tuple(names))) is not None:
        for match in pattern.finditer(seen):
            add(*match.span())
    for _, pattern in LABELS.items():
        for match in pattern.finditer(seen):
            if _NAME_LABEL.search(match.group(0)):  # "Name:", "Account holders": all after it
                spans.append((match.end(), len(seen)))
            else:
                spans.append((match.end(), _tokens_after(seen, match.end(), keep, False)))
    return spans


def _placeholder(n: int) -> str:
    letters = ""
    n += 1
    while n:
        n, rest = divmod(n - 1, 26)
        letters = chr(ord("a") + rest) + letters
    return f"[hidden-{letters}]"


def prepare_outbound(text: str, *, names: Sequence[str] = ()) -> MaskedLine | None:
    """The one way statement text is made ready for a model: `normalise` it, then mask every
    detail in that normalised string (`mask_line`). Detection and masking run on the string
    that is sent, so nothing is mapped back onto the printed text. None: the line can't be sent
    at all and is withheld.

    Every outbound path uses it: each statement line the AI reader is sent (screenshots and
    vision transcripts included; retry feedback quotes only these lines) and each CSV heading in
    a layout sketch (whose cells are type tokens, never values)."""
    return mask_line(text, names=names)


def mask_line(text: str, *, names: Sequence[str] = ()) -> MaskedLine | None:
    """`text` as it may be sent to the AI reader, with every account detail in it (a sort code,
    account or card number, card ending, IBAN, postcode, street address, a household name, a
    title and name, or the value after a label such as "Customer ref") replaced by a
    placeholder. Amounts and dates are never touched. A line without details comes back
    unchanged. Details are found exactly as `classify` finds them.

    None when the line can't be sent this way: it is a balance or limit line, a detail would be
    left showing, or what is left once the details are taken out is a balance (an address row
    with the summary box printed on it). Such a line is withheld and reported instead."""
    masked = _mask(text, names)
    if masked is None or _residue_is_balance(masked):
        return None
    return masked


def is_masked_balance(text: str, *, names: Sequence[str] = ()) -> bool:
    """A balance or limit line with a detail on it: what is left once the details are taken
    out is a balance ("Flat 3   Minimum payment £25.00", an address row with the summary box
    printed on it). It is withheld like any balance line, never a row."""
    if is_balance_line(text):
        return True
    masked = _mask(text, names)
    return masked is not None and _residue_is_balance(masked)


def _residue_is_balance(masked: MaskedLine) -> bool:
    return bool(masked.hidden) and is_balance_line(
        " ".join(_PLACEHOLDER.sub(" ", masked.text).split())
    )


def _mask(text: str, names: Sequence[str]) -> MaskedLine | None:
    normal, seen = _seen(text)
    found = _classify_seen(seen, names)
    keep = _kept(seen)
    numbers = _long_runs(seen)
    if not found and not numbers:
        return MaskedLine(text=normal)
    if "balance_line" in found or _is_holder(normal, names):
        return None
    try:
        spans = [*_detail_spans(seen, keep, names), *numbers] if found else numbers
    except _Unmaskable:
        return None
    hide = [False] * len(normal)
    for start, end in spans:
        for k in range(max(0, start), min(end, len(normal))):
            if not keep[k]:
                hide[k] = True
    runs: list[tuple[int, int]] = []
    k = 0
    while k < len(normal):
        if not hide[k]:
            k += 1
            continue
        start = k
        while k < len(normal) and (hide[k] or (_gap(normal[k]) and _hidden_after(hide, normal, k))):
            k += 1
        end = k
        while end > start and _gap(normal[end - 1]):
            end -= 1
        while start < end and _gap(normal[start]):
            start += 1
        if start < end:
            runs.append((start, end))
    hidden: dict[str, str] = {}
    out: list[str] = []
    at = 0
    for n, (start, end) in enumerate(runs):
        placeholder = _placeholder(n)
        hidden[placeholder] = normal[start:end]
        out += [normal[at:start], placeholder]
        at = end
    out.append(normal[at:])
    masked = "".join(out)
    if not _clean(masked, names):
        return None
    return MaskedLine(text=masked, hidden=hidden)


def _gap(ch: str) -> bool:
    return ch.isspace() or unicodedata.category(ch) == "Cf"


def _hidden_after(hide: Sequence[bool], text: str, k: int) -> bool:
    """A space inside a run of hidden characters (the gap in "12-34-56 87654321")."""
    j = k
    while j < len(text) and _gap(text[j]):
        j += 1
    return j < len(text) and hide[j]


def unmasked_label(text: str) -> bool:
    """A label in `text` with a value still showing after it: a number after "Sort code" or
    "Customer ref", or anything but amounts and dates after "Name:" or "Account holders"."""
    seen = _seen(text)[1]
    keep = _kept(seen)
    for pattern in LABELS.values():
        for match in pattern.finditer(seen):
            if _NAME_LABEL.search(match.group(0)):
                rest = "".join(ch for k, ch in enumerate(seen) if k >= match.end() and not keep[k])
                if _PLACEHOLDER.sub("", rest).strip(" :,&"):
                    return True
            elif _tokens_after(seen, match.end(), keep, False) > match.end():
                return True
    return False


def _clean(masked: str, names: Sequence[str]) -> bool:
    """No detail is left in a masked line: no value class, no label with a value after it (an
    amount or a date after a label is fine), and no run of six or more digits."""
    seen = _seen(masked)[1]
    return (
        not (classify(masked, names=names) - LABEL_CLASSES)
        and not unmasked_label(masked)
        and not _long_runs(seen)
    )
