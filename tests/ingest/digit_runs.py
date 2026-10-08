"""The test oracle for R-M3-23 (a): no run of six or more digits from a printed line survives in
anything sent, nor the last four digits of one. Written apart from `sensitive` on purpose, so
the guard doesn't share a blind spot with the code it guards (re-review 2 R5: its joins and its
reading of a line's own date are its own, not copies of the masker's).

A run is digits joined by at most one separator at a time: a space, or one punctuation mark
with a space either side or none ("20-11-33 87654321", "1234/5678", "87.65.43.21",
"8765 - 4321", "8765,4321", "8765+4321", "8765#4321", "8765\\4321", "8765:4321"), whatever
letters it is glued to ("ACC87654321"). Characters a scan reads for digits (O, S, l, I, |, B, Z)
count as digits inside a mostly-digit token ("876S4321"), never in a word ("BOOTS"). Not runs:
a well-formed amount (12.30, 1234.56, 1,234.56, £250: one point, two decimals, comma grouping
only), a real date (02/10/2026, 05.10.2026, 2026-10-02, 2 Oct 2026), and the line's own date
printed with spaces or dots at its start ("05 10 26", "05.10.26 06.10.26" with a posting date
after it), but only when no group of four or more digits is joined after it: in "20.11.33
87654321" the first group is a sort code."""

from __future__ import annotations

import contextlib
import re
import unicodedata

MIN_DIGITS = 6
TAIL = 4
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_DATE = re.compile(
    r"(?<![\d.])(?:\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})|\d{1,2}[-.]\d{1,2}[-.]\d{4}"
    r"|\d{4}-\d{1,2}-\d{1,2})(?![\d])"
    rf"|(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\b(?:\s+\d{{4}}(?!\d))?",
    re.IGNORECASE,
)
_DAY = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s+"
_SPACED_DATE = re.compile(r"(\d{1,2})([ .])(\d{1,2})\2(\d{4}|\d{2})(?!\d)")
_DAY_NAME = re.compile(_DAY, re.IGNORECASE)
_AMOUNT = re.compile(
    r"[£$€]\s?\d[\d,]*(?:\.\d{2})?(?!\d)"
    r"|(?<![\d,])(?<!\d\.)(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d]|[.,]\d)"
)
_JOIN = r"(?: ?[-/.,·_+#\\:] ?| )"
_RUN = re.compile(rf"\d(?:{_JOIN}?\d)*")
_JOINED = re.compile(rf"{_JOIN}(\d(?:{_JOIN}?\d)*)")
_LOOKALIKE = str.maketrans("OoSslI|BZz", "0055111822")
_TOKEN = re.compile(r"[A-Za-z0-9|]+")


def _digitish(token: str) -> str:
    """A token as a scan's digits: in each piece between its words (two or more letters, one
    of them not a look-alike: "SMITH", "RENT"), look-alikes read as digits when at least half
    the piece is digits."""
    if not any(ch.isdigit() for ch in token):
        return token
    looks = set("OoSslI|BZz")
    out, piece = [], ""
    for chunk in re.split(r"([A-Za-z|]+)", token):
        is_word = len(chunk) >= 2 and chunk.isascii() and any(c not in looks for c in chunk)
        if is_word and chunk[0].isalpha():
            out.append(_as_digits(piece))
            out.append(chunk)
            piece = ""
        else:
            piece += chunk
    out.append(_as_digits(piece))
    return "".join(out)


def _as_digits(piece: str) -> str:
    if piece and 2 * sum(ch.isdigit() for ch in piece) >= len(piece):
        return piece.translate(_LOOKALIKE)
    return piece


def fold(text: str) -> str:
    """Digits in any script or style as ASCII digits; invisible and enclosing marks gone;
    look-alikes in mostly-digit tokens read as digits."""
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        if unicodedata.category(ch) in ("Cf", "Mn", "Me"):
            continue
        if ch.isdigit() and not ch.isascii():
            with contextlib.suppress(TypeError, ValueError):
                ch = str(unicodedata.digit(ch))
        out.append(ch)
    text = " ".join("".join(out).split())
    return _TOKEN.sub(lambda m: _digitish(m.group(0)), text)


def _date_at(text: str, at: int) -> int | None:
    """End of a day-month-year date printed with spaces or dots that starts at `at`."""
    match = _SPACED_DATE.match(text, at)
    if match and 1 <= int(match.group(1)) <= 31 and 1 <= int(match.group(3)) <= 12:
        return match.end()
    return None


def _joined_after(text: str, at: int) -> int:
    """How many digits the number joined to the text at `at` holds (0 when none is)."""
    match = _JOINED.match(text, at)
    return sum(ch.isdigit() for ch in match.group(1)) if match else 0


def own_dates(text: str) -> int:
    """Where the line's own dates end (0 when it doesn't start with one): a date printed with
    spaces or dots at its start, or two of them (the transaction and the posting date), when
    no group of four or more digits is joined after the last."""
    day = _DAY_NAME.match(text)
    first = _date_at(text, day.end() if day else 0)
    if first is None:
        return 0
    second = _date_at(text, first + 1) if text[first : first + 1] == " " else None
    if second is not None and _joined_after(text, second) < 4:
        return second
    return first if _joined_after(text, first) < 4 else 0


def _blank(match: re.Match[str]) -> str:
    return " " * len(match.group(0))


def _without_dates_and_amounts(text: str) -> str:
    return _AMOUNT.sub(_blank, _DATE.sub(_blank, text))


def _plain(text: str) -> str:
    """`text` folded, with its dates and amounts blanked out, and then its own dates."""
    text = _without_dates_and_amounts(fold(text))
    own = own_dates(text)
    return " " * own + text[own:]


def _digit_runs(plain: str) -> list[str]:
    return [re.sub(r"\D", "", m.group(0)) for m in _RUN.finditer(plain)]


def runs(text: str) -> list[str]:
    """The digits of each run of six or more in `text`, dates and amounts left out."""
    return [r for r in _digit_runs(_plain(text)) if len(r) >= MIN_DIGITS]


def survivors(printed: str, sent: str) -> list[str]:
    """Runs of `printed` of which six digits in a row, or the last four, still appear among
    the digits of `sent` (each of its runs read whole, dates and amounts left out). What is
    sent is read without the leading-date exemption, except for the very dates the printed
    line starts with: a sort code kept at the start of what is sent is caught."""
    printed_text = _without_dates_and_amounts(fold(printed))
    shown_text = _without_dates_and_amounts(fold(sent))
    own = own_dates(printed_text)
    if own and shown_text.startswith(printed_text[:own]):
        shown_text = " " * own + shown_text[own:]
    shown = _digit_runs(shown_text)
    leaked = []
    for run in runs(printed):
        windows = {run[i : i + MIN_DIGITS] for i in range(len(run) - MIN_DIGITS + 1)}
        if any(w in s for w in windows for s in shown) or any(run[-TAIL:] in s for s in shown):
            leaked.append(run)
    return leaked
