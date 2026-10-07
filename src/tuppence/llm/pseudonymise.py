"""Optional stand-ins for names and numbers before cloud calls (spec §4.6).

Consistent tokens keep the agent's reasoning intact ("Adult A pays ACCT_2"); the mapping
never leaves the machine and replies are restored locally. Merchants, amounts and dates
are deliberately left alone. This module is pure: no I/O.
"""

from __future__ import annotations

import re
import string
from typing import Any

_IBAN = re.compile(r"\bGB\d{2}\s?[A-Z]{4}(?:\s?\d){14}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_SORT = re.compile(r"\b\d{2}-\d{2}-\d{2}\b")
_ACCOUNT = re.compile(r"(?i)\b(acc(?:ount)?|a/c)(\.?\s*(?:no\.?|number|num)?\s*[:#]?\s*)(\d{8})\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b")
_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b")


def role_labels(people: list[tuple[str, str]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    adults = children = dependants = 0
    for name, role in people:
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


def _loose(text: str) -> str:
    """Regex for ``text`` that tolerates any run of whitespace between words."""
    return r"\s+".join(re.escape(part) for part in text.split())


class Pseudonymiser:
    def __init__(
        self, people: list[tuple[str, str]], hidden_names: list[str] | None = None
    ) -> None:
        self.names = role_labels(people)
        for i, name in enumerate(hidden_names or [], start=1):
            self.names.setdefault(name, f"Person {i}")
        self.forward: dict[str, str] = {}
        # Role labels are always restorable, even if the model mentions someone the prompt didn't.
        self.labels: dict[str, str] = {label: name for name, label in self.names.items()}
        # Issued number/identifier tokens only (matched case-insensitively on restore).
        self.reverse: dict[str, str] = {}
        self.counters: dict[str, int] = {}
        self.count = 0

    def _token(self, kind: str, original: str, digits: str | None = None) -> str:
        key = f"{kind}:{re.sub(r'[\s-]', '', original)}"
        if key not in self.forward:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            base = f"{kind}_{self.counters[kind]}"
            token = f"{base} ending ••{digits[-2:]}" if digits else base
            self.forward[key] = token
            self.reverse[token] = original
            if digits:
                self.reverse[base] = original
        self.count += 1
        return self.forward[key]

    def redact(self, text: str) -> str:
        text = _IBAN.sub(lambda m: self._token("IBAN", m.group(0)), text)
        text = _SORT.sub(lambda m: self._token("SORTCODE", m.group(0)), text)
        text = _ACCOUNT.sub(
            lambda m: m.group(1) + m.group(2) + self._token("ACCT", m.group(3), m.group(3)),
            text,
        )

        def card(m: re.Match[str]) -> str:
            digits = re.sub(r"\D", "", m.group(0))
            if not 13 <= len(digits) <= 19:
                return m.group(0)
            return self._token("ACCT", m.group(0), digits)

        text = _CARD.sub(card, text)
        text = _EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), text)
        text = _PHONE.sub(lambda m: self._token("PHONE", m.group(0)), text)
        text = _POSTCODE.sub(lambda m: self._token("POSTCODE", m.group(0)), text)
        for name in sorted(self.names, key=len, reverse=True):
            pattern = re.compile(r"(?<!\w)" + _loose(name) + r"(?!\w)", re.IGNORECASE)

            def swap(_m: re.Match[str], name: str = name) -> str:
                self.count += 1
                return self.names[name]

            text = pattern.sub(swap, text)
        return text

    def restore(self, text: str) -> str:
        """Put originals back in one pass.

        Only stand-ins this instance issued are restored. Number tokens match
        case-insensitively with flexible spacing; people labels match exactly. Both must
        stand alone (no word characters either side), so ``SORTCODE_1`` never matches
        inside ``SORTCODE_12`` and ``Adult A`` never inside ``Adult Attendance``.
        """
        if not self.reverse and not self.labels:
            return text
        lowered = {re.sub(r"\s+", " ", t).lower(): o for t, o in self.reverse.items()}
        parts: list[str] = []
        if self.reverse:
            tokens = sorted(self.reverse, key=len, reverse=True)
            parts.append("(?i:" + "|".join(_loose(t) for t in tokens) + ")")
        if self.labels:
            labels = sorted(self.labels, key=len, reverse=True)
            parts.append("|".join(_loose(lab) for lab in labels))
        pattern = re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)")

        def swap(m: re.Match[str]) -> str:
            found = re.sub(r"\s+", " ", m.group(0))
            if found in self.labels:
                return self.labels[found]
            return lowered[found.lower()]

        return pattern.sub(swap, text)

    def restore_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, list):
            return [self.restore_value(v) for v in value]
        if isinstance(value, dict):
            return {k: self.restore_value(v) for k, v in value.items()}
        return value
