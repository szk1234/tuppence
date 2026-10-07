"""Optional stand-ins for names and numbers before cloud calls (spec §4.6).

Consistent tokens keep the agent's reasoning intact ("Adult A pays ACCT_2"); the mapping
never leaves the machine and replies are restored locally. Merchants, amounts and dates
are deliberately left alone. This module is pure: no I/O.

Name matching rules (ruling R-M1b-15): multi-word names match case-insensitively; a
single word (a first name, a surname, or a one-word name) matches only as written or in
ALL CAPS, so "will" stays an ordinary word while a statement's "WILL SMITH" is masked.
Text is NFKC-normalised first and both accented and unaccented spellings match; restore
always returns the configured spelling.
"""

from __future__ import annotations

import re
import string
import unicodedata
from typing import Any

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})

# Generic IBAN (any country), case-insensitive, applied before the card pattern.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?\b", re.IGNORECASE)
# Sort codes written 12-34-56 or 12 34 56; not part of a longer column of digit pairs.
_SORT = re.compile(r"(?<!\d)(?<!\b\d\d[ -])\d{2}[ -]\d{2}[ -]\d{2}(?!\d|-\d| \d{2}(?!\d))")
_SORT_KEYWORD = re.compile(
    r"(?<!\w)(sort\s?code|s/c|sc)(?!\w)([^\n\d]{0,20})(?<!\d)(\d{6})(?!\d)", re.IGNORECASE
)
_ACCT_DIGITS = r"(\d{4} \d{4}|\d{8})(?!\d)(?![.,]\d)"
_ACCOUNT = re.compile(
    r"(?<!\w)(account|acct|acc|a/c)(?!\w)([^\n\d]{0,25})(?<![£$€.,\d])" + _ACCT_DIGITS,
    re.IGNORECASE,
)
_ACCT_AFTER_SORT = re.compile(r"(SORTCODE_\d+[ \t,;/-]{1,3})(?<![£$€.,\d])" + _ACCT_DIGITS)
_ACCT_BEFORE_SORT = re.compile(
    r"(?<![\d£$€.,])(\d{4} \d{4}|\d{8})(?!\d)([ \t,;/-]{1,3}SORTCODE_\d+)"
)
_CARD_RUN = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
_CARD_SEPARATED = re.compile(r"(?<!\d)(?:\d{1,7}[ -]){2,}\d{1,7}(?!\d)")
# Start only at the beginning of a run and never backtrack into it: linear on long inputs.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]++@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b")
_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b")

_MAX_SEPARATED_GROUPS = 24


def _norm(text: str) -> str:
    """NFKC, straight apostrophes, single spaces, trimmed (for names)."""
    return " ".join(_norm_text(text).split())


def _norm_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_APOSTROPHES)


def _key(name: str) -> str:
    return _norm(name).casefold()


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _spellings(text: str) -> list[str]:
    plain = _strip_accents(text)
    return [text] if plain == text else [text, plain]


def _single_forms(word: str) -> list[str]:
    """A single word matches as written or in ALL CAPS, in both spellings."""
    forms: list[str] = []
    for spelling in _spellings(word):
        for form in (spelling, spelling.upper()):
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

    def _cards_separated(self, m: re.Match[str]) -> str:
        text = m.group(0)
        pieces = [(g.start(), g.end()) for g in re.finditer(r"\d+", text)]
        if len(pieces) > _MAX_SEPARATED_GROUPS:
            return text
        out: list[str] = []
        pos = 0
        i = 0
        while i < len(pieces):
            found = None
            for end in range(len(pieces), i, -1):
                window = pieces[i:end]
                # Only the whole run, or card-shaped groups (4+ digits), may be a card.
                shaped = (
                    all(b - a >= 4 for a, b in window[:-1]) and window[-1][1] - window[-1][0] >= 3
                )
                if not (shaped or (i == 0 and end == len(pieces))):
                    continue
                span = "".join(text[a:b] for a, b in window)
                if 13 <= len(span) <= 19 and _luhn(span):
                    found = (end, span)
                    break
            if found is None:
                i += 1
                continue
            end, span = found
            start_char, end_char = pieces[i][0], pieces[end - 1][1]
            out.append(text[pos:start_char])
            out.append(self._token("ACCT", span, span))
            pos = end_char
            i = end
        out.append(text[pos:])
        return "".join(out)

    def redact(self, text: str) -> str:
        text = _norm_text(text)
        text = _IBAN.sub(lambda m: self._token("IBAN", m.group(0)), text)
        text = _SORT.sub(lambda m: self._token("SORTCODE", m.group(0)), text)
        text = _SORT_KEYWORD.sub(
            lambda m: m.group(1) + m.group(2) + self._token("SORTCODE", m.group(3)), text
        )
        text = _ACCOUNT.sub(lambda m: m.group(1) + m.group(2) + self._acct(m.group(3)), text)
        text = _ACCT_AFTER_SORT.sub(lambda m: m.group(1) + self._acct(m.group(2)), text)
        text = _ACCT_BEFORE_SORT.sub(lambda m: self._acct(m.group(1)) + m.group(2), text)
        text = _CARD_RUN.sub(lambda m: self._acct(m.group(0)), text)
        text = _CARD_SEPARATED.sub(self._cards_separated, text)
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
