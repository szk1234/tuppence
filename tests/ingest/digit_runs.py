"""The test oracle for R-M3-23 (a): no run of six or more digits from a printed line survives in
anything sent. Written apart from `sensitive` on purpose, so the guard doesn't share a blind
spot with the code it guards.

A run is digits joined by at most one space, hyphen, slash, middle dot or underscore at a time
("20-11-33 87654321", "1234/5678", "8765 4321"), whatever letters it is glued to ("ACC87654321").
An amount (12.30, 1,234.56, £250) and a real date (02/10/2026, 2026-10-02, 2 Oct 2026, and
05 10 26 or 05.10.26 with a real day and month) are not runs. A run survives when any six of
its digits in a row still appear in the sent text."""

from __future__ import annotations

import contextlib
import re
import unicodedata

MIN_DIGITS = 6
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_DATE = re.compile(
    r"(?<!\d)(?:\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})|\d{1,2}[-.]\d{1,2}[-.]\d{4}|\d{4}-\d{1,2}-\d{1,2})"
    rf"(?!\d)|(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\b(?:\s+\d{{4}}(?!\d))?",
    re.IGNORECASE,
)
# A date printed with spaces or dots ("05 10 26", "20.11.33") when it reads as a day and month.
_SPACED_DATE = re.compile(r"(?<![\d.])(\d{1,2})([ .])(\d{1,2})\2(\d{4}|\d{2})(?![\d])")
_AMOUNT = re.compile(
    r"[£$€]\s?\d[\d,]*(?:\.\d{2})?(?!\d)|(?<![\d,])\d{1,3}(?:,\d{3})+\.\d{2}(?!\d)"
    r"|(?<![\d.])\d+\.\d{2}(?![\d.])"
)
_RUN = re.compile(r"\d(?:[ \-/·_]?\d)*")


def fold(text: str) -> str:
    """Digits in any script or style as ASCII digits; invisible and enclosing marks gone."""
    out = []
    for ch in unicodedata.normalize("NFKC", text):
        if unicodedata.category(ch) in ("Cf", "Mn", "Me"):
            continue
        if ch.isdigit() and not ch.isascii():
            with contextlib.suppress(TypeError, ValueError):
                ch = str(unicodedata.digit(ch))
        out.append(ch)
    return " ".join("".join(out).split())


def _plain(text: str) -> str:
    """`text` folded, with its dates and amounts blanked out."""
    text = fold(text)
    text = _DATE.sub(lambda m: " " * len(m.group(0)), text)
    text = _SPACED_DATE.sub(
        lambda m: (
            " " * len(m.group(0))
            if 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(3)) <= 12
            else m.group(0)
        ),
        text,
    )
    return _AMOUNT.sub(lambda m: " " * len(m.group(0)), text)


def runs(text: str) -> list[str]:
    """The digits of each run of six or more in `text`, dates and amounts left out."""
    found = [re.sub(r"\D", "", m.group(0)) for m in _RUN.finditer(_plain(text))]
    return [r for r in found if len(r) >= MIN_DIGITS]


def survivors(printed: str, sent: str) -> list[str]:
    """Runs of `printed` of which six digits in a row still appear among the digits of `sent`
    (each of its runs read whole, dates and amounts left out)."""
    shown = [re.sub(r"\D", "", m.group(0)) for m in _RUN.finditer(_plain(sent))]
    leaked = []
    for run in runs(printed):
        windows = {run[i : i + MIN_DIGITS] for i in range(len(run) - MIN_DIGITS + 1)}
        if any(w in s for w in windows for s in shown):
            leaked.append(run)
    return leaked
