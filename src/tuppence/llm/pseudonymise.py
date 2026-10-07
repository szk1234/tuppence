"""Optional stand-ins for names and numbers before cloud calls (spec §4.6).

Consistent tokens keep the agent's reasoning intact ("Adult A pays ACCT_2"); the mapping
never leaves the machine and replies are restored locally. Merchants, amounts and dates
are deliberately left alone. This module is pure: no I/O.

Name matching rules (rulings R-M1b-15/17): multi-word names match case-insensitively; a
single word (a first name, a surname, or a one-word name) matches as written, Title-case or
ALL CAPS, so a lower-case "will" stays an ordinary word while "Will" and "WILL" are masked.
Text is NFKC-normalised first and both accented and unaccented spellings match; restore
always returns the configured spelling.

Numbers: cards (contiguous 13+ digits; separated runs shaped like cards, or Luhn-valid) run
before accounts so a card is never split, and overlapping candidates are masked together so
no digits are left beside a stand-in; any whitespace run or Unicode dash separates groups. A
sort code (2-2-2, or six bare digits leading) next to an 8-digit account on the same line is
masked as a pair with no keyword (R-M1b-17/18). Otherwise a date-shaped 2-2-2 group is a sort
code only near a sort-code keyword (R-M1b-16/17).
"""

from __future__ import annotations

import re
import string
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from datetime import date
from typing import Any

_TEXT_MAP = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "ʼ": "'",
        # every Unicode dash and the minus sign become a plain hyphen-minus
        **{c: "-" for c in "‐‑‒–—―−﹘﹣－"},
        # invisible characters that would split a number
        **{c: None for c in "­​‌‍⁠﻿"},
    }
)

# One separator between digit groups: spaces/tabs, optionally around a hyphen, or a hyphen.
_SEP = r"(?:[ \t]++(?:-[ \t]*+)?|-[ \t]*+)"

# IBAN registry lengths by country: a hint for where an IBAN ends. Any other country is still
# accepted (15-34 characters ending on a group boundary) when the mod-97 checksum passes.
_IBAN_LENGTHS = {
    country: int(length)
    for country, length in re.findall(
        r"([A-Z]{2})(\d{2})",
        "AD24 AE23 AL28 AT20 AZ28 BA20 BE16 BG22 BH22 BR29 BY28 CH21 CR22 CY28 CZ24 DE22  "
        "DK18 DO28 EE20 EG29 ES24 FI18 FO18 FR27 GB22 GE22 GI23 GL18 GR27 GT28 HR21 HU28  "
        "IE22 IL23 IQ23 IS26 IT27 JO30 KW30 KZ20 LB28 LC32 LI21 LT20 LU20 LV21 MC27 MD24  "
        "ME22 MK19 MR27 MT31 MU30 NL18 NO15 PK24 PL28 PS29 PT25 QA29 RO24 RS22 SA24 SC31  "
        "SE24 SI19 SK24 SM27 ST25 SV28 TL23 TN24 TR26 UA29 VA22 VG24 XK20",
    )
}
# ASCII only: with Unicode case folding "İ" would match [A-Z].
_IBAN = re.compile(r"(?<!\d)[A-Z]{2}\d{2}(?:[ \t-]{0,3}[A-Z0-9]){11,30}", re.IGNORECASE | re.ASCII)
_IBAN_DIGITS = str.maketrans({c: str(int(c, 36)) for c in string.digits + string.ascii_uppercase})
_IBAN_STRIP = str.maketrans("", "", " \t-")
_IBAN_SEP = re.compile(r"[ \t-]+")

_SORT_WORD = re.compile(
    r"(?<!\w)(?:sort[ \t-]{0,2}code|sort/acc(?:ount)?|s/c|sc)(?!\w)", re.IGNORECASE
)
# A number rule never starts inside one of our own stand-ins ("IBAN_12 34 56 ...").
_NOT_IN_STAND_IN = r"(?<!IBAN_)(?<!ACCT_)(?<!SORTCODE_)"
_SORT_FORM = rf"\d{{2}}{_SEP}\d{{2}}{_SEP}\d{{2}}"
# An account number: 8 digits, or 4 + 4 with any separator. Never the integer part of an
# amount ("12345678.00"); a comma after it is a list or CSV separator, not a decimal point.
_ACCT_FORM = rf"(?:\d{{4}}{_SEP}\d{{4}}|\d{{8}})(?!\d)(?!\.\d)"
# What joins a sort code and an account number on one line: spaces/tabs or one punctuation
# mark (dash, comma, semicolon, slash, ampersand, plus, bar), optionally then "and".
_JOIN = r"(?:[ \t]++|[ \t]*+[-,;/&+|][ \t]*+)(?:(?i:and)[ \t]++)?"
# A sort code (2-2-2, or six bare digits) next to an account number, in either order:
# masked as a pair with no keyword (rulings R-M1b-17/18). Six bare digits only lead.
_PAIR = re.compile(
    rf"{_NOT_IN_STAND_IN}(?<![\d£$€.•])(?:"
    rf"(?P<sc>{_SORT_FORM}|\d{{6}})(?!\d)(?P<j1>{_JOIN})(?P<ac>{_ACCT_FORM})"
    rf"|(?P<ac2>{_ACCT_FORM})(?P<j2>{_JOIN})(?P<sc2>{_SORT_FORM})(?!\d)(?!\.\d))"
)
# Every 2-2-2 group, overlapping ones included; ``_mask_sort_codes`` decides which are codes.
_SORT_AT = re.compile(rf"{_NOT_IN_STAND_IN}(?<![\d•])(?=({_SORT_FORM})(?!\d))")
# Neighbouring digit pairs with the same kind of separator make a column (dates, tables).
_COL_SPACE_BEFORE = re.compile(r"(?<![\w.,£$€•])\d\d[ \t]++$")
_COL_SPACE_AFTER = re.compile(r"[ \t]++\d{2}(?!\d)")
_COL_DASH_BEFORE = re.compile(r"(?<![\w.,£$€•])\d\d[ \t]*+-[ \t]*+$")
_COL_DASH_AFTER = re.compile(r"[ \t]*+-[ \t]*+\d")
# "2026-12-31 14:22": the tail of an ISO date is never a sort code.
_ISO_YEAR_BEFORE = re.compile(r"(?<!\d)(?:19|20)\d\d[ \t]*+-[ \t]*+$")
_SORT_KEYWORD = re.compile(
    r"(?<!\w)(sort[ \t-]{0,2}code|sort/acc(?:ount)?|s/c|sc)(?!\w)"
    r"([^\n\d]{0,40})(?<![\d£$€.])(\d{6})(?!\d)(?!\.\d)",
    re.IGNORECASE,
)
# Six bare digits followed, on the same line, by a sort-code keyword ("090128 is my sort code").
_SORT_KEYWORD_AFTER = re.compile(
    r"(?<![\d£$€.])(\d{6})(?!\d)(?!\.\d)(?=[^\n\d]{0,20}"
    r"(?<!\w)(?:sort[ \t-]{0,2}code|sort/acc(?:ount)?|s/c|sc)(?!\w))",
    re.IGNORECASE,
)
_ACCOUNT = re.compile(
    rf"(?<!\w)(account|acct|acc|a/c)(?!\w)([^\n\d]{{0,40}})(?<![£$€.\d])({_ACCT_FORM})",
    re.IGNORECASE,
)
_ACCOUNT_NUMBER_WORD = re.compile(r"(?:number|num|no)\.?[\s:#.]*$", re.IGNORECASE)
_ACCT_AFTER_SORT = re.compile(rf"(SORTCODE_\d+{_JOIN})({_ACCT_FORM})")
_ACCT_BEFORE_SORT = re.compile(rf"(?<![\d£$€.•])({_ACCT_FORM})({_JOIN}SORTCODE_\d+)")
# Any contiguous run of 13 or more digits: over-long runs are masked whole, never skipped.
_CARD_RUN = re.compile(r"(?<!\d)\d{13,}(?!\d)")
_CARD_SEPARATED = re.compile(rf"{_NOT_IN_STAND_IN}(?<!\d)\d{{1,19}}(?:{_SEP}\d{{1,19}})+(?!\d)")
# Group sizes of real card layouts: masked without a Luhn check.
_CARD_SHAPES = {(4, 4, 4, 4), (4, 6, 5), (4, 6, 4), (4, 4, 4, 4, 3)}
# Start only at the beginning of a run and never backtrack into it: linear on long inputs.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]++@[\w-]+(?:\.[\w-]+)+\b")
# UK mobiles, and any +44 / 0044 number (optional "(0)"); spaces or dashes between digits.
_PHONE = re.compile(
    r"(?:\+|(?<![\d+])00)44[ \t-]*+(?:\(0\)[ \t-]*+)?\d(?:[ \t-]?\d){8,9}(?!\d)"
    r"|(?<!\d)07\d{3}[ \t-]?\d{3}[ \t-]?\d{3}(?!\d)"
)
# Full postcodes in capitals or in lower case.
_POSTCODE = re.compile(
    r"\b(?:[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}|[a-z]{1,2}\d[a-z\d]?\s?\d[a-z]{2})\b"
)

_MAX_SEPARATED_GROUPS = 24


def _norm(text: str) -> str:
    """NFKC, straight apostrophes, single spaces, trimmed (for names)."""
    return " ".join(_norm_text(text).split())


def _norm_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_TEXT_MAP)


def _key(name: str) -> str:
    return _norm(name).casefold()


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _spellings(text: str) -> list[str]:
    plain = _strip_accents(text)
    return [text] if plain == text else [text, plain]


def _single_forms(word: str) -> list[str]:
    """A single word matches as written, Title-case or ALL CAPS, in both spellings."""
    forms: list[str] = []
    for spelling in _spellings(word):
        for form in (
            spelling,
            spelling[:1].upper() + spelling[1:].lower(),
            spelling.title(),
            spelling.upper(),
        ):
            if form not in forms:
                forms.append(form)
    return forms


def _alt(forms: list[str]) -> str:
    return "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))


def _loose(text: str, sep: str = r"\s+") -> str:
    return sep.join(re.escape(part) for part in text.split())


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _mod97_ok(compact: str) -> bool:
    return int((compact[4:] + compact[:4]).translate(_IBAN_DIGITS)) % 97 == 1


def _iban_length(text: str, start: int, candidate: str) -> int:
    """Characters of ``candidate`` that form a valid IBAN (0 if none)."""
    compact = candidate.translate(_IBAN_STRIP).upper()
    wanted = _IBAN_LENGTHS.get(compact[:2])
    if wanted is not None:
        lengths = [wanted] if wanted <= len(compact) else []
    else:
        # Unknown country: only where a group ends (before a separator, or at the end of the
        # candidate with no letter or digit following), longest first.
        lengths = []
        removed = 0
        for sep in _IBAN_SEP.finditer(candidate):
            count = sep.start() - removed
            removed += sep.end() - sep.start()
            if 15 <= count <= 34:
                lengths.append(count)
        end_at = start + len(candidate)
        if 15 <= len(compact) <= 34 and not text[end_at : end_at + 1].isalnum():
            lengths.append(len(compact))
        lengths.sort(reverse=True)
    for length in lengths:
        if _mod97_ok(compact[:length]):
            seen = 0
            for i, ch in enumerate(candidate):
                if ch not in " \t-":
                    seen += 1
                    if seen == length:
                        return i + 1
    return 0


def _is_date8(digits: str) -> bool:
    """True for a plausible YYYYMMDD date (1990 to next year)."""
    return (
        len(digits) == 8
        and 1990 <= int(digits[:4]) <= date.today().year + 1
        and 1 <= int(digits[4:6]) <= 12
        and 1 <= int(digits[6:]) <= 31
    )


def _is_year_pair(digits: str) -> bool:
    """True for a tax or academic year written in full ("2025-2026")."""
    first, second = int(digits[:4]), int(digits[4:])
    return 1990 <= first <= date.today().year + 1 and second == first + 1


def _line_finder(text: str) -> Callable[[int], tuple[int, int]]:
    """Return a function giving the (start, end) of the line holding a position."""
    newlines = [n.start() for n in re.finditer("\n", text)]

    def bounds(pos: int) -> tuple[int, int]:
        i = bisect_right(newlines, pos - 1)
        start = newlines[i - 1] + 1 if i else 0
        end = newlines[i] if i < len(newlines) else len(text)
        return start, end

    return bounds


def _clean_people(people: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Strip, drop blanks, dedupe (after normalisation); first spelling and role win."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for name, role in people:
        display = name.strip()
        k = _key(display)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append((display, role))
    return out


def role_labels(people: list[tuple[str, str]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    adults = children = dependants = 0
    for name, role in _clean_people(people):
        if role == "adult":
            labels[name] = f"Adult {string.ascii_uppercase[adults % 26]}"
            adults += 1
        elif role == "child":
            children += 1
            labels[name] = f"Child {children}"
        else:
            dependants += 1
            labels[name] = f"Dependant {dependants}"
    return labels


class Pseudonymiser:
    def __init__(
        self, people: list[tuple[str, str]], hidden_names: list[str] | None = None
    ) -> None:
        self.names = role_labels(people)
        person_names = list(self.names)
        by_key = {_key(n): label for n, label in self.names.items()}
        hidden = 0
        for raw in hidden_names or []:
            name = raw.strip()
            k = _key(name)
            if not k or k in by_key:
                continue
            hidden += 1
            self.names[name] = by_key[k] = f"Person {hidden}"
        self.forward: dict[str, str] = {}
        # Role labels are always restorable, even if the model mentions someone the prompt didn't.
        self.labels: dict[str, str] = {label: name for name, label in self.names.items()}
        self._label_lookup = {re.sub(r"[\s-]+", " ", lab.casefold()): lab for lab in self.labels}
        # Issued number/identifier tokens only (matched case-insensitively on restore).
        self.reverse: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.count = 0
        self._name_labels: list[str] = []
        self._name_re = self._build_name_pattern(set(person_names))
        self._restore_cache: tuple[int, re.Pattern[str]] | None = None

    def _build_name_pattern(self, person_names: set[str]) -> re.Pattern[str] | None:
        # source -> (rank, label, is_full_name)
        entries: dict[str, tuple[int, str, bool]] = {}
        conflicted: set[str] = set()

        def add(source: str, rank: int, label: str, full: bool) -> None:
            current = entries.get(source)
            if current is None:
                entries[source] = (rank, label, full)
            elif current[1] != label:
                if full and not current[2]:
                    entries[source] = (rank, label, True)
                elif not full and not current[2]:
                    conflicted.add(source)

        for name, label in self.names.items():
            words = _norm(name).split(" ")
            if len(words) == 1:
                add("(?:" + _alt(_single_forms(words[0])) + ")", 2, label, True)
                continue
            spelled = [
                "\\s+".join(re.escape(w) for w in s.split()) for s in _spellings(_norm(name))
            ]
            add("(?i:" + "|".join(spelled) + ")", 0, label, True)
            if name not in person_names:
                continue
            first, surname = words[0], words[-1]
            if len(first) >= 3:
                add("(?:" + _alt(_single_forms(first)) + ")", 2, label, False)
            if len(surname) >= 3:
                surname_alt = _alt(_single_forms(surname))
                add("(?:" + surname_alt + ")", 2, label, False)
                initials = _alt(sorted({c for c in _single_forms(first[0]) if len(c) == 1}))
                add(rf"(?:{initials})(?:\.\s*|\s+)(?:{surname_alt})", 1, label, False)
        kept = [(s, v) for s, v in entries.items() if s not in conflicted or v[2]]
        kept.sort(key=lambda item: (item[1][0], -len(item[0])))
        if not kept:
            return None
        self._name_labels = [v[1] for _, v in kept]
        body = "|".join(f"({s})" for s, _ in kept)
        # Bounded by letters and "_" only: "SMITH1" is still a name, "IBAN_1" never is.
        return re.compile(r"(?<![^\W\d])(?:" + body + r")(?![^\W\d])")

    def _token(self, kind: str, original: str, digits: str | None = None) -> str:
        key = f"{kind}:{re.sub(r'[\s-]', '', original).casefold()}"
        if key not in self.forward:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            base = f"{kind}_{self.counters[kind]}"
            token = f"{base} ending ••{digits[-2:]}" if digits else base
            self.forward[key] = token
            self.reverse[token] = original
            if digits:
                self.reverse[base] = original
            self._restore_cache = None
        self.count += 1
        return self.forward[key]

    def _acct(self, raw: str) -> str:
        digits = re.sub(r"\D", "", raw)
        return self._token("ACCT", digits, digits)

    def _ibans(self, text: str) -> str:
        """Mask IBANs; overlapping valid candidates become one stand-in.

        Every candidate start is tried, including starts inside an IBAN already found, so a
        reference that passes mod-97 by chance cannot swallow the front of a real IBAN and
        leave its tail in the clear.
        """
        out: list[str] = []
        pos = search = 0
        span: tuple[int, int] | None = None

        def flush(start: int, end: int) -> None:
            nonlocal pos
            out.append(text[pos:start])
            out.append(self._token("IBAN", text[start:end]))
            pos = end

        while (m := _IBAN.search(text, search)) is not None:
            if span is not None and m.start() >= span[1]:
                flush(*span)
                span = None
            length = _iban_length(text, m.start(), m.group(0))
            if length:
                end = m.start() + length
                span = (m.start(), end) if span is None else (span[0], max(span[1], end))
            search = m.start() + 1
        if span is not None:
            flush(*span)
        out.append(text[pos:])
        return "".join(out)

    def _mask_sort_codes(self, text: str) -> str:
        """Mask 2-2-2 groups that are sort codes.

        Candidates overlap, so a rejected group ("12 40-47" in "£45.12 40-47-84") never
        hides the real code after it. A group that is not date-shaped is a sort code unless
        it sits in a column of digit pairs joined the same way, or ends an ISO date. A
        date-shaped group is a sort code only with a sort-code keyword earlier on its line or
        within 20 characters after it. (Groups next to an account number were already
        masked as pairs.)
        """
        kws = [(k.start(), k.end()) for k in _SORT_WORD.finditer(text)]
        kw_starts = [k[0] for k in kws]
        kw_ends = [k[1] for k in kws]
        line_bounds = _line_finder(text)

        def is_sort_code(start: int, end: int, code: str) -> bool:
            if _ISO_YEAR_BEFORE.search(text, max(0, start - 12), start):
                return False
            line_start, line_end = line_bounds(start)
            i = bisect_right(kw_ends, start) - 1
            if i >= 0 and kw_ends[i] > line_start:
                return True
            j = bisect_right(kw_starts, end - 1)
            if j < len(kws) and kws[j][0] - end <= 20 and kws[j][0] < line_end:
                return True
            first, second = (int(n) for n in re.findall(r"\d+", code)[:2])
            if 1 <= first <= 31 and 1 <= second <= 12:
                return False  # date-shaped
            before, after = (
                (_COL_DASH_BEFORE, _COL_DASH_AFTER)
                if "-" in code
                else (_COL_SPACE_BEFORE, _COL_SPACE_AFTER)
            )
            return (
                before.search(text, max(0, start - 12), start) is None
                and after.match(text, end) is None
            )

        out: list[str] = []
        last = 0
        for m in _SORT_AT.finditer(text):
            start = m.start()
            if start < last:
                continue
            code = m.group(1)
            end = start + len(code)
            if is_sort_code(start, end, code):
                out.append(text[last:start])
                out.append(self._token("SORTCODE", code))
                last = end
        out.append(text[last:])
        return "".join(out)

    def _pair(self, m: re.Match[str]) -> str:
        if m.group("sc") is not None:
            return self._token("SORTCODE", m.group("sc")) + m.group("j1") + self._acct(m["ac"])
        return self._acct(m["ac2"]) + m.group("j2") + self._token("SORTCODE", m.group("sc2"))

    def _mask_accounts(self, text: str) -> str:
        """Mask numbers after an account keyword.

        A date-like number (YYYYMMDD, or a year range such as 2025-2026) is left alone
        unless the keyword says "number"/"no" or its line also gives a sort code.
        """
        line_bounds = _line_finder(text)
        marks = sorted(
            [k.start() for k in _SORT_WORD.finditer(text)]
            + [k.start() for k in re.finditer("SORTCODE_", text)]
        )

        def sort_code_on_line(pos: int) -> bool:
            start, end = line_bounds(pos)
            i = bisect_left(marks, start)
            return i < len(marks) and marks[i] < end

        def swap(m: re.Match[str]) -> str:
            raw = m.group(3)
            digits = re.sub(r"\D", "", raw)
            lead = m.group(1) + m.group(2)
            dated = _is_date8(digits) or (not raw.isdigit() and _is_year_pair(digits))
            if dated and not _ACCOUNT_NUMBER_WORD.search(lead) and not sort_code_on_line(m.start()):
                return lead + raw
            return lead + self._acct(digits)

        return _ACCOUNT.sub(swap, text)

    def _cards_separated(self, m: re.Match[str]) -> str:
        """Mask the card numbers in a run of separated digit groups.

        Windows of real card layouts (with or without Luhn) and Luhn-valid windows of other
        card-like layouts are candidates. Windows that pass Luhn in a card layout win; any
        other candidate overlapping one of them is dropped, and the remaining overlapping
        candidates are masked together, so no card digits are ever left beside a stand-in.
        A sort code + account pair that passes Luhn by chance is left to the pair rule.
        """
        text = m.group(0)
        pieces = [(g.start(), g.end()) for g in re.finditer(r"\d+", text)]
        if len(pieces) > _MAX_SEPARATED_GROUPS:
            # Too long to window-scan cheaply: fail closed and mask the whole run.
            return self._acct(text)
        lens = [b - a for a, b in pieces]
        strong: list[tuple[int, int]] = []
        weak: list[tuple[int, int]] = []
        for i in range(len(pieces)):
            if i and lens[i] < 4:
                continue
            total = 0
            for end in range(i + 1, len(pieces) + 1):
                total += lens[end - 1]
                if total > 19:
                    break
                window = tuple(lens[i:end])
                card_shape = window in _CARD_SHAPES
                if not card_shape:
                    # Other layouts need card-like groups (or the whole run), and Luhn.
                    if total < 13:
                        continue
                    shaped = all(n >= 4 for n in window[:-1]) and window[-1] >= 3
                    if not (shaped or (i == 0 and end == len(pieces))):
                        continue
                digits = "".join(text[a:b] for a, b in pieces[i:end])
                if not _luhn(digits):
                    if card_shape:
                        weak.append((i, end))
                    continue
                if card_shape:
                    strong.append((i, end))
                    continue
                pair = _PAIR.match(m.string, m.start() + pieces[i][0])
                if pair is None or pair.end() != m.start() + pieces[end - 1][1]:
                    weak.append((i, end))
        spans = strong + [w for w in weak if not any(w[0] < e and s < w[1] for s, e in strong)]
        merged: list[tuple[int, int]] = []
        for s, e in sorted(spans):
            if merged and s < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        out: list[str] = []
        pos = 0
        for s, e in merged:
            digits = "".join(text[a:b] for a, b in pieces[s:e])
            out.append(text[pos : pieces[s][0]])
            out.append(self._token("ACCT", digits, digits))
            pos = pieces[e - 1][1]
        out.append(text[pos:])
        return "".join(out)

    def redact(self, text: str) -> str:
        text = _norm_text(text)
        text = self._ibans(text)
        # Cards first, so an account or sort-code rule can never take half of one.
        text = _CARD_RUN.sub(lambda m: self._acct(m.group(0)), text)
        text = _CARD_SEPARATED.sub(self._cards_separated, text)
        text = _PAIR.sub(self._pair, text)
        text = self._mask_sort_codes(text)
        text = _SORT_KEYWORD.sub(
            lambda m: m.group(1) + m.group(2) + self._token("SORTCODE", m.group(3)), text
        )
        text = _SORT_KEYWORD_AFTER.sub(lambda m: self._token("SORTCODE", m.group(1)), text)
        text = self._mask_accounts(text)
        text = _ACCT_AFTER_SORT.sub(lambda m: m.group(1) + self._acct(m.group(2)), text)
        text = _ACCT_BEFORE_SORT.sub(lambda m: self._acct(m.group(1)) + m.group(2), text)
        text = _EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), text)
        text = _PHONE.sub(lambda m: self._token("PHONE", m.group(0)), text)
        text = _POSTCODE.sub(lambda m: self._token("POSTCODE", m.group(0)), text)
        if self._name_re is not None:

            def swap(m: re.Match[str]) -> str:
                self.count += 1
                return self._name_labels[(m.lastindex or 1) - 1]

            text = self._name_re.sub(swap, text)
        return text

    def _restore_pattern(self) -> re.Pattern[str] | None:
        if self._restore_cache is not None and self._restore_cache[0] == len(self.reverse):
            return self._restore_cache[1]
        parts: list[str] = []
        if self.reverse:
            tokens = sorted(self.reverse, key=len, reverse=True)
            parts.append("|".join(_loose(t) for t in tokens))
        if self.labels:
            labels = sorted(self.labels, key=len, reverse=True)
            parts.append("|".join(_loose(lab, r"[\s-]+") for lab in labels))
        if not parts:
            return None
        pattern = re.compile(r"(?<!\w)(?i:" + "|".join(parts) + r")(?!\w)")
        self._restore_cache = (len(self.reverse), pattern)
        return pattern

    def restore(self, text: str) -> str:
        """Put originals back in one pass.

        Only stand-ins this instance issued are restored (plus the configured people
        labels). Matching is case-insensitive, tolerates extra spacing and a hyphen in
        labels, and requires the stand-in to stand alone (no word characters either
        side), so ``SORTCODE_1`` never matches inside ``SORTCODE_12`` nor ``Adult A``
        inside ``Adult Attendance``. Names come back in their configured spelling.
        """
        pattern = self._restore_pattern()
        if pattern is None:
            return text
        lowered = {re.sub(r"\s+", " ", t).lower(): o for t, o in self.reverse.items()}

        def swap(m: re.Match[str]) -> str:
            tokenish = re.sub(r"\s+", " ", m.group(0)).lower()
            if tokenish in lowered:
                return lowered[tokenish]
            label = self._label_lookup[re.sub(r"[\s-]+", " ", m.group(0).casefold())]
            return self.labels[label]

        return pattern.sub(swap, text)

    def restore_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, list):
            return [self.restore_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self.restore_value(v) for k, v in value.items()}
        return value
