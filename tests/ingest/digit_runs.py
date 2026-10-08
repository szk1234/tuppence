"""The test oracle for R-M3-23 (a): no run of six or more digits from a printed line survives in
anything sent, nor the last four digits of one. Written apart from `sensitive` on purpose, so
the guard doesn't share a blind spot with the code it guards.

A run is digits joined by at most one space, hyphen, slash, dot, middle dot or underscore at a
time ("20-11-33 87654321", "1234/5678", "87.65.43.21"), whatever letters it is glued to
("ACC87654321"). Characters a scan reads for digits (O, S, l, I, |, B, Z) count as digits inside
a mostly-digit token ("876S4321"), never in a word ("BOOTS"). Not runs: a well-formed amount
(12.30, 1234.56, 1,234.56, £250: one point, two decimals, comma grouping only), a real date
(02/10/2026, 05.10.2026, 2026-10-02, 2 Oct 2026), and a date printed with spaces or dots
("05 10 26", "20.11.33") where it is the line's own date, at its start."""

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
_LEADING_DATE = re.compile(
    rf"^(?:{_DAY})?(\d{{1,2}})([ .])(\d{{1,2}})\2(\d{{4}}|\d{{2}})(?![\d])", re.IGNORECASE
)
_AMOUNT = re.compile(
    r"[£$€]\s?\d[\d,]*(?:\.\d{2})?(?!\d)"
    r"|(?<![\d,])(?<!\d\.)(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d]|[.,]\d)"
)
_RUN = re.compile(r"\d(?:[ \-/.·_]?\d)*")
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


def _blank(match: re.Match[str]) -> str:
    return " " * len(match.group(0))


def _plain(text: str) -> str:
    """`text` folded, with its amounts and dates blanked out."""
    text = fold(text)
    lead = _LEADING_DATE.match(text)
    if lead and 1 <= int(lead.group(1)) <= 31 and 1 <= int(lead.group(3)) <= 12:
        text = " " * lead.end() + text[lead.end() :]
    text = _DATE.sub(_blank, text)
    return _AMOUNT.sub(_blank, text)


def runs(text: str) -> list[str]:
    """The digits of each run of six or more in `text`, dates and amounts left out."""
    found = [re.sub(r"\D", "", m.group(0)) for m in _RUN.finditer(_plain(text))]
    return [r for r in found if len(r) >= MIN_DIGITS]


def survivors(printed: str, sent: str) -> list[str]:
    """Runs of `printed` of which six digits in a row, or the last four, still appear among
    the digits of `sent` (each of its runs read whole, dates and amounts left out)."""
    shown = [re.sub(r"\D", "", m.group(0)) for m in _RUN.finditer(_plain(sent))]
    leaked = []
    for run in runs(printed):
        windows = {run[i : i + MIN_DIGITS] for i in range(len(run) - MIN_DIGITS + 1)}
        if any(w in s for w in windows for s in shown) or any(run[-TAIL:] in s for s in shown):
            leaked.append(run)
    return leaked
