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
before accounts so a card is never split; any whitespace run or Unicode dash separates
groups; a date-shaped 2-2-2 group is a sort code only near a sort-code keyword or an
8-digit account (R-M1b-16/17).
"""

from __future__ import annotations

import re
import string
import unicodedata
from bisect import bisect_right
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
# Any 2-2-2 group; ``_mask_sort_codes`` decides whether it is a sort code.
_SORT_ANY = re.compile(rf"(?<![\d•])\d{{2}}{_SEP}\d{{2}}{_SEP}\d{{2}}(?!\d)")
_SORT_COL_BEFORE = re.compile(rf"(?<![\w.,£$€•])\d\d{_SEP}$")
_SORT_COL_AFTER = re.compile(r"(?:-\d|[ \t]++\d{2}(?!\d))")
_ACCT8_AT = re.compile(
    rf"{_SEP}(?:\d{{4}}[ \t]++\d{{4}}(?![ \t]++\d{{4}})|\d{{8}})(?!\d)(?![.,]\d)"
)
_SORT_KEYWORD = re.compile(
    r"(?<!\w)(sort[ \t-]{0,2}code|sort/acc(?:ount)?|s/c|sc)(?!\w)"
    r"([^\n\d]{0,40})(?<![\d£$€.,])(\d{6})(?!\d)(?![.,]\d)",
    re.IGNORECASE,
)
# Six bare digits followed, on the same line, by a sort-code keyword ("090128 is my sort code").
_SORT_KEYWORD_AFTER = re.compile(
    r"(?<![\d£$€.,])(\d{6})(?!\d)(?![.,]\d)(?=[^\n\d]{0,20}"
    r"(?<!\w)(?:sort[ \t-]{0,2}code|sort/acc(?:ount)?|s/c|sc)(?!\w))",
    re.IGNORECASE,
)
_ACCT_DIGITS = r"(\d{4}[ \t]++\d{4}(?![ \t]++\d{4})|\d{8})(?!\d)(?![.,]\d)"
_ACCOUNT = re.compile(
    r"(?<!\w)(account|acct|acc|a/c)(?!\w)([^\n\d]{0,25})(?<![£$€.,\d])" + _ACCT_DIGITS,
    re.IGNORECASE,
)
_ACCOUNT_NUMBER_WORD = re.compile(r"(?:number|num|no)\.?[\s:#.]*$", re.IGNORECASE)
_ACCT_AFTER_SORT = re.compile(
    r"(SORTCODE_\d+[ \t,;/&+-]{1,6}(?:and[ \t]+)?)"
    r"(?<![£$€.,\d])" + _ACCT_DIGITS,
    re.IGNORECASE,
)
_ACCT_BEFORE_SORT = re.compile(
    r"(?<![\d£$€.,])(\d{4}[ \t]++\d{4}(?![ \t]++\d{4})|\d{8})(?!\d)([ \t,;/-]{1,6}SORTCODE_\d+)"
)
# Any contiguous run of 13 or more digits: over-long runs are masked whole, never skipped.
_CARD_RUN = re.compile(r"(?<!\d)\d{13,}(?!\d)")
_CARD_SEPARATED = re.compile(rf"(?<!\d)\d{{1,19}}(?:{_SEP}\d{{1,19}})+(?!\d)")
# Group sizes of real card layouts: masked without a Luhn check.
_CARD_SHAPES = {(4, 4, 4, 4), (4, 6, 5), (4, 6, 4), (4, 4, 4, 4, 3)}
# Start only at the beginning of a run and never backtrack into it: linear on long inputs.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]++@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b")
_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b")

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
        return re.compile(r"(?<!\w)(?:" + body + r")(?!\w)")

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
        out: list[str] = []
        pos = search = 0
        while (m := _IBAN.search(text, search)) is not None:
            length = _iban_length(text, m.start(), m.group(0))
            if length:
                out.append(text[pos : m.start()])
                out.append(self._token("IBAN", text[m.start() : m.start() + length]))
                pos = search = m.start() + length
            else:
                search = m.start() + 1
        out.append(text[pos:])
        return "".join(out)

    def _mask_sort_codes(self, text: str) -> str:
        """Mask 2-2-2 groups that are sort codes.

        A group that is not date-shaped is a sort code (unless it sits in a column of digit
        pairs and no keyword helps). A date-shaped group is a sort code only with a sort-code
        keyword earlier on its line or within 20 characters after it, or when an 8-digit
        account number follows it.
        """
        kws = [(k.start(), k.end()) for k in _SORT_WORD.finditer(text)]
        kw_starts = [k[0] for k in kws]
        kw_ends = [k[1] for k in kws]
        newlines = [n.start() for n in re.finditer("\n", text)]

        def line_bounds(pos: int) -> tuple[int, int]:
            i = bisect_right(newlines, pos - 1)
            start = newlines[i - 1] + 1 if i else 0
            end = newlines[i] if i < len(newlines) else len(text)
            return start, end

        def swap(m: re.Match[str]) -> str:
            first, second = (int(n) for n in re.findall(r"\d+", m.group(0))[:2])
            date_shaped = 1 <= first <= 31 and 1 <= second <= 12
            line_start, line_end = line_bounds(m.start())
            i = bisect_right(kw_ends, m.start()) - 1
            keyword = i >= 0 and kw_ends[i] > line_start
            j = bisect_right(kw_starts, m.end() - 1)
            if not keyword and j < len(kws):
                keyword = kws[j][0] - m.end() <= 20 and kws[j][0] < line_end
            account_next = _ACCT8_AT.match(text, m.end()) is not None
            column = (
                _SORT_COL_BEFORE.search(text, max(0, m.start() - 12), m.start()) is not None
                or _SORT_COL_AFTER.match(text, m.end()) is not None
            )
            if keyword or account_next or not (date_shaped or column):
                return self._token("SORTCODE", m.group(0))
            return m.group(0)

        return _SORT_ANY.sub(swap, text)

    def _account(self, m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(3))
        lead = m.group(1) + m.group(2)
        if _is_date8(digits) and not _ACCOUNT_NUMBER_WORD.search(lead):
            return lead + m.group(3)
        return lead + self._acct(digits)

    def _cards_separated(self, m: re.Match[str]) -> str:
        text = m.group(0)
        pieces = [(g.start(), g.end()) for g in re.finditer(r"\d+", text)]
        if len(pieces) > _MAX_SEPARATED_GROUPS:
            # Too long to window-scan cheaply: fail closed and mask the whole run.
            return self._acct(text)
        lens = [b - a for a, b in pieces]
        out: list[str] = []
        pos = i = 0
        while i < len(pieces):
            best = 0
            if i == 0 or lens[i] >= 4:
                total = 0
                for end in range(i + 1, len(pieces) + 1):
                    total += lens[end - 1]
                    if total > 19:
                        break
                    window = lens[i:end]
                    if tuple(window) in _CARD_SHAPES:
                        best = end
                    elif total >= 13:
                        # Other shapes need the whole run, or card-like groups, and Luhn.
                        shaped = all(n >= 4 for n in window[:-1]) and window[-1] >= 3
                        if shaped or (i == 0 and end == len(pieces)):
                            digits = "".join(text[a:b] for a, b in pieces[i:end])
                            if _luhn(digits):
                                best = end
            if best == 0:
                i += 1
                continue
            digits = "".join(text[a:b] for a, b in pieces[i:best])
            out.append(text[pos : pieces[i][0]])
            out.append(self._token("ACCT", digits, digits))
            pos = pieces[best - 1][1]
            i = best
        out.append(text[pos:])
        return "".join(out)

    def redact(self, text: str) -> str:
        text = _norm_text(text)
        text = self._ibans(text)
        # Cards first, so an account or sort-code rule can never take half of one.
        text = _CARD_RUN.sub(lambda m: self._acct(m.group(0)), text)
        text = _CARD_SEPARATED.sub(self._cards_separated, text)
        text = self._mask_sort_codes(text)
        text = _SORT_KEYWORD.sub(
            lambda m: m.group(1) + m.group(2) + self._token("SORTCODE", m.group(3)), text
        )
        text = _SORT_KEYWORD_AFTER.sub(lambda m: self._token("SORTCODE", m.group(1)), text)
        text = _ACCOUNT.sub(self._account, text)
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
