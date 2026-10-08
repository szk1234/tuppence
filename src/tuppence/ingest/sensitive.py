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
    "iban": re.compile(r"\b[A-Z]{2}\d{2}\s?[A-Z0-9]{4}(?:\s?\d{4}){2,}(?:\s?[A-Z0-9]{1,4})?\b", _I),
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


# --- one view of a line for every filter ------------------------------------------------------
#
# Every filter reads a line through the same view: compatibility forms folded (NFKC: full-width
# and other styled digits and letters become plain ones), invisible format characters dropped
# (a zero-width space between two letters becomes a space), curly apostrophes made straight,
# and every run of whitespace (non-breaking and narrow spaces too) one space. Classifying and
# masking both use it, so a detail one of them sees, the other sees too.

_APOSTROPHES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u201b": "'"})
_PLAIN = re.compile(r"[^\x20-\x7e]|\s\s")  # beyond printable ASCII, or a run of spaces


def _fold(text: str, *, collapse: bool) -> tuple[str, list[int] | None]:
    """`text` folded as above, and for each character of the result the index of the
    character it came from (None when nothing changed)."""
    if not _PLAIN.search(text):
        return text, None
    chars: list[str] = []
    where: list[int] = []
    for i, ch in enumerate(text):
        if unicodedata.category(ch) == "Cf":  # zero-width and other invisible characters
            between_letters = bool(chars) and chars[-1].isalpha() and text[i + 1 : i + 2].isalpha()
            if not between_letters:
                continue
            piece = " "
        else:
            piece = ch if ch.isascii() else unicodedata.normalize("NFKC", ch)
        for c in piece.translate(_APOSTROPHES):
            if c.isspace():
                if collapse and chars and chars[-1] == " ":
                    continue
                c = " " if (collapse or c not in "\t") else c
            chars.append(c)
            where.append(i)
    return "".join(chars), where


def view(text: str) -> str:
    """The line as every filter reads it (see above)."""
    return _fold(text, collapse=True)[0]


def normalise(text: str) -> str:
    """The line as Tuppence keeps and sends it: folded like `view`, but with its runs of spaces
    (a PDF's column gaps) kept."""
    return _fold(text, collapse=False)[0]


@lru_cache(maxsize=64)
def _name_pattern(names: tuple[str, ...]) -> re.Pattern[str] | None:
    """Any printed form of a household name, anywhere in a line."""
    forms = sorted(_name_forms(tuple(view(n) for n in names)), key=len, reverse=True)
    if not forms:
        return None
    alternatives = (r"[\s.,]+".join(re.escape(w) for w in form.split()) for form in forms)
    return re.compile(r"(?<![\w'])(?:" + "|".join(alternatives) + r")(?![\w'])", _I)


def _is_holder(text: str, names: Sequence[str]) -> bool:
    """A line that is only one of the household's names (in any of its printed forms)."""
    if not names or any(ch.isdigit() for ch in text):
        return False
    return " ".join(_words(view(text))) in _name_forms(tuple(view(n) for n in names))


def _spelled_sort_codes(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _SPELLED_SORT_CODE.finditer(text):
        day, month = int(match.group(1)), int(match.group(3))
        if not (1 <= day <= 31 and 1 <= month <= 12):
            spans.append(match.span())
    return spans


def _classify_view(seen: str, names: Sequence[str]) -> set[str]:
    found = {name for name, pattern in (*LABELS.items(), *VALUES.items()) if pattern.search(seen)}
    if _spelled_sort_codes(seen):
        found.add("sort_code")
    if names and (pattern := _name_pattern(tuple(names))) is not None and pattern.search(seen):
        found.add("holder_name")
    return found


def classify(text: str, *, names: Sequence[str] = ()) -> set[str]:
    """The sensitive classes in `text` (empty when there are none), read through `view`.
    `names` are the household's own names: one of them anywhere in the line, in any printed
    form, is a holder name."""
    return _classify_view(view(text), names)


def is_balance_line(text: str) -> bool:
    """A line that is only balances or limits and their figures ("Balance £1,234.56",
    "£1,184.56 available", "Balance £1,234.56 Available £1,184.56")."""
    return _BALANCE_LINE.search(view(text)) is not None


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
    """`text` as it may be shown to a model (a CSV heading): HIDDEN when it holds any sensitive
    class, else its `view` with every run of four or more digits replaced by <NUM>."""
    if is_sensitive(text, names=names):
        return HIDDEN
    return _DIGIT_RUN.sub("<NUM>", view(text))


# --- masking details inside a transaction line (I2) -------------------------------------------
#
# Masking reads the line through the same `view` as `classify`, finds every detail with the
# same patterns, and hides the characters of the original line each one came from. So the
# placeholder covers the whole detail however it was printed (a zero-width space or a
# non-breaking space inside it), and a detail `classify` sees is a detail `mask_line` hides.

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


def _kept(text: str) -> list[bool]:
    keep = [False] * len(text)
    for match in _KEEP_MONEY.finditer(text):
        keep[match.start() : match.end()] = [True] * (match.end() - match.start())
    for match in _KEEP_DATE.finditer(text):
        value = match.group(0)
        # dd-mm-yy or dd.mm.yy may be a sort code: only a date that reads as one is kept
        numeric = "/" not in value and not re.search(r"[A-Za-z]", value)
        if numeric and parse_date(value) is None:
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
    seen, where = _fold(text, collapse=True)
    found = _classify_view(seen, names)
    if not found:
        return MaskedLine(text=text)
    if "balance_line" in found or _is_holder(text, names):
        return None
    keep = _kept(seen)
    try:
        spans = _detail_spans(seen, keep, names)
    except _Unmaskable:
        return None
    hidden_view = [False] * len(seen)
    for start, end in spans:
        for k in range(max(0, start), min(end, len(seen))):
            if not keep[k]:
                hidden_view[k] = True
    # Each run of hidden characters of the view hides every character of the line it came
    # from, the invisible ones and the gaps between them included.
    hide = [False] * len(text)
    k = 0
    while k < len(seen):
        if not hidden_view[k]:
            k += 1
            continue
        run = k
        while k < len(seen) and hidden_view[k]:
            k += 1
        first, last = (run, k - 1) if where is None else (where[run], where[k - 1])
        for j in range(first, last + 1):
            hide[j] = True
    runs: list[tuple[int, int]] = []
    k = 0
    while k < len(text):
        if not hide[k]:
            k += 1
            continue
        start = k
        while k < len(text) and (hide[k] or (_gap(text[k]) and _hidden_after(hide, text, k))):
            k += 1
        end = k
        while end > start and _gap(text[end - 1]):
            end -= 1
        while start < end and _gap(text[start]):
            start += 1
        if start < end:
            runs.append((start, end))
    hidden: dict[str, str] = {}
    out: list[str] = []
    at = 0
    for n, (start, end) in enumerate(runs):
        placeholder = _placeholder(n)
        hidden[placeholder] = text[start:end]
        out += [text[at:start], placeholder]
        at = end
    out.append(text[at:])
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
    seen = view(text)
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
    """No detail is left in a masked line: no value class, and no label with a value after it
    (an amount or a date after a label is fine)."""
    return not (classify(masked, names=names) - LABEL_CLASSES) and not unmasked_label(masked)
