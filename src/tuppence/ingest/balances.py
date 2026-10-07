"""Balances and signs read on this device, from lines that never go to a model.

The summary box of a statement ("Opening balance on 01/10/2026 £1,000.00", "Closing balance
£1,857.82") is withheld from the AI reader, so its figures are read here with the same money
rules as the rest of the ingest package. A figure is used only when it is unambiguous: one
label, one amount, nothing else between them. The same balances let `repair_signs` settle a
row's direction deterministically: when a row prints a running balance and the balance before it
is known, the difference says whether money came in or went out.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from tuppence.ingest.models import (
    CheckLevel,
    Document,
    Line,
    ParsedRow,
    ParsedStatement,
    Perspective,
)
from tuppence.ingest.textnum import parse_money, to_pence

_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DATE = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})[a-z]*\.?(?:\s+\d{{2,4}})?\b"
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)
_OPENING = r"(?:opening|previous|starting|start)\s+balance|balance\s+brought\s+forward"
_CLOSING = r"(?:closing|new|ending|end)\s+balance|balance\s+carried\s+forward"
_LABEL = re.compile(
    rf"\b(?P<opening>{_OPENING})\b|\b(?P<closing>{_CLOSING})\b|\bbrought\s+forward\b",
    re.IGNORECASE,
)
_MONEY = re.compile(
    r"\(?\s*[+\-−–]?\s*(?:£|\$|€)?\s*[+\-−–]?\s*"
    r"\d{1,3}(?:,\d{3})*\.\d{2}(?![\d.])\s*\)?\s*[\-−–]?(?:\s*(?:CR|DR)\b)?",
    re.IGNORECASE,
)
_FILLER = re.compile(r"^(?:\s|:|\.|-|\(|\)|on|at|as|is|of|date)*$", re.IGNORECASE)
_MARKER = re.compile(r"\b(CR|DR)\b", re.IGNORECASE)
_BROUGHT_FORWARD = re.compile(r"brought\s+forward", re.IGNORECASE)
_OUT_LABELS = ("Paid out", "Money out", "Withdrawals", "Withdrawal", "Debits", "Debit")
_IN_LABELS = ("Paid in", "Money in", "Deposits", "Deposit", "Credits", "Credit")
_PAGE = re.compile(r"P(\d+)L\d+")


@dataclass(frozen=True)
class LocalBalances:
    opening: int | None = None
    closing: int | None = None


def _balance_pence(token: str, perspective: Perspective) -> int | None:
    """The printed figure as the statement model stores it: a bank account in credit is
    positive (CR keeps it so; DR, brackets or a minus are overdrawn), a card's balance owed is
    positive and CR means the card is in credit."""
    value = parse_money(token.strip())
    if value is None:
        return None
    marker = _MARKER.search(token)
    kind = marker.group(1).upper() if marker else ""
    if perspective == "card":
        magnitude = abs(value)
        negative = kind == "CR" or (value < 0 and kind != "DR")
        return to_pence(-magnitude if negative else magnitude)
    return to_pence(value)


def _figure(tail: str, perspective: Perspective) -> int | None:
    """The one amount that follows a label, or None when anything else is in the way."""
    plain = _DATE.sub(" ", tail)
    match = _MONEY.search(plain)
    if match is None or not _FILLER.match(plain[: match.start()]):
        return None
    return _balance_pence(match.group(0), perspective)


def local_balances(doc: Document, *, perspective: Perspective) -> LocalBalances:
    """Opening and closing balance from the withheld lines (the summary box). A figure that
    appears twice with different values, or that isn't plainly labelled, is left out."""
    by_ref = doc.by_ref()
    found: dict[str, set[int]] = {"opening": set(), "closing": set()}
    unclear: set[str] = set()
    for ref in doc.preamble_refs:
        line = by_ref.get(ref)
        if line is None:
            continue
        labels = list(_LABEL.finditer(line.text))
        for i, label in enumerate(labels):
            side = "closing" if label.group("closing") else "opening"
            if label.group("opening") is None and label.group("closing") is None:
                continue  # a bare "brought forward" is not a summary label
            end = labels[i + 1].start() if i + 1 < len(labels) else len(line.text)
            pence = _figure(line.text[label.end() : end], perspective)
            if pence is None:
                unclear.add(side)
            else:
                found[side].add(pence)
    return LocalBalances(
        opening=_only(found["opening"], "opening" in unclear),
        closing=_only(found["closing"], "closing" in unclear),
    )


def _only(values: set[int], unclear: bool) -> int | None:
    return next(iter(values)) if len(values) == 1 and not unclear else None


def _has_printed_sign(text: str) -> bool:
    cleaned = text.strip()
    return (
        cleaned.startswith(("-", "−", "–", "+", "("))
        or cleaned.endswith(("-", "−", ")"))
        or _MARKER.search(cleaned) is not None
    )


def _page_text(ref: str, lines: Sequence[Line]) -> str:
    match = _PAGE.match(ref)
    if not match:
        return "\n".join(line.text for line in lines)
    return "\n".join(
        line.text for line in lines if (m := _PAGE.match(line.ref)) and m.group(1) == match.group(1)
    )


def _label_for(positive: bool, row: ParsedRow, lines: Sequence[Line]) -> str | None:
    text = _page_text(row.ref, lines)
    for label in _IN_LABELS if positive else _OUT_LABELS:
        found = re.search(rf"\b{re.escape(label)}\b", text, re.IGNORECASE)
        if found:
            return found.group(0)
    return None


def _carried(doc: Document, ref: str) -> int | None:
    """A skipped 'balance brought forward' line's figure: the balance before the next row."""
    line = doc.by_ref().get(ref)
    if line is None or not _BROUGHT_FORWARD.search(line.text):
        return None
    plain = _DATE.sub(" ", line.text)
    figures = _MONEY.findall(plain)
    return _balance_pence(figures[-1], "household") if figures else None


@dataclass
class SignRepair:
    repaired: list[str]
    errors: list[str]


def repair_signs(
    doc: Document, parsed: ParsedStatement, *, opening: int | None, level: CheckLevel
) -> SignRepair:
    """Settle the direction of unsigned amounts from the running balance.

    A row whose printed amount carries no sign, and whose balance is printed, moved the balance
    from the one before it: the difference gives the direction. Rows are in file order, so the
    balance before the first row is the opening balance (or a skipped 'balance brought
    forward' line), and before any other row the balance printed on the row above. Rows are
    changed in place. A first row whose direction can't be proved is reported.
    """
    result = SignRepair([], [])
    if level == "screenshot" or parsed.perspective == "card":
        return result
    skipped = {s.ref for s in parsed.skipped}
    by_base: dict[str, list[ParsedRow]] = {}
    for row in parsed.rows:
        by_base.setdefault(row.ref.split("#", 1)[0], []).append(row)
    previous = opening
    for ref in doc.data_refs:
        if ref in skipped and (figure := _carried(doc, ref)) is not None:
            previous = figure
            continue
        for row in by_base.get(ref, []):
            previous = _settle(doc, row, previous, result)
    return result


def _settle(doc: Document, row: ParsedRow, previous: int | None, result: SignRepair) -> int | None:
    if row.edited:
        return row.balance_after_pence
    after = row.balance_after_pence
    if after is None:
        return None if previous is None else previous + row.amount_pence
    unsigned = not _has_printed_sign(row.amount_text)
    if previous is None:
        if unsigned:
            result.errors.append(
                f"{row.ref}: can't tell whether this is money in or out, because the balance "
                "before it isn't printed or couldn't be read"
            )
        return after
    delta = after - previous
    if unsigned and delta != row.amount_pence and abs(delta) == abs(row.amount_pence):
        row.amount_pence = delta
        row.sign_from = _label_for(delta > 0, row, doc.lines)
        result.repaired.append(row.ref)
    return after
