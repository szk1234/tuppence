"""Balances and signs read on this device, from lines that never go to a model.

The summary box of a statement ("Opening balance on 01/10/2026 £1,000.00", "Closing balance
£1,857.82") is withheld from the AI reader, so its figures are read here with the same money
rules as the rest of the ingest package. A figure is used only when it is unambiguous: one
label, one amount, nothing else between them. The same balances let `repair_signs` prove a
row's direction: when the balances printed either side of some rows are known, only certain
directions add up. A direction that can't be proved is reported, never guessed.
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
_CARRIED = re.compile(r"(?:brought|carried)\s+forward", re.IGNORECASE)
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
    """A skipped 'balance brought (or carried) forward' line's figure: the balance at that
    point, read on this device."""
    line = doc.by_ref().get(ref)
    if line is None or not _CARRIED.search(line.text):
        return None
    plain = _DATE.sub(" ", line.text)
    figures = _MONEY.findall(plain)
    return _balance_pence(figures[-1], "household") if figures else None


@dataclass
class SignRepair:
    repaired: list[str]
    errors: list[str]


MAX_UNSIGNED = 16  # rows between two printed balances that can still be settled exactly


def repair_signs(
    doc: Document,
    parsed: ParsedStatement,
    *,
    opening: int | None,
    closing: int | None = None,
    level: CheckLevel,
) -> SignRepair:
    """Prove the direction of amounts printed without a sign, from printed balances.

    `opening` and `closing` must be read on this device (never the model's values). Rows are
    taken in file order and grouped up to each printed balance (a row's running balance, or a
    skipped 'brought/carried forward' line); the opening balance starts the first group and
    the closing balance ends the last. For each group whose unsigned amounts have a direction
    to prove:
    - with no balance known before or after it, the direction can't be proved: reported;
    - a single row between two known balances takes its direction from the difference, and
      is repaired if the model read it the other way;
    - several rows are proved only when exactly one choice of directions adds up; when more
      than one does, they are reported. A unique answer the model got wrong is left for
      Check, which reports the balance that doesn't add up.
    Rows are changed in place.
    """
    result = SignRepair([], [])
    if level == "screenshot" or parsed.perspective == "card":
        return result
    skipped = {s.ref for s in parsed.skipped}
    by_base: dict[str, list[ParsedRow]] = {}
    for row in parsed.rows:
        by_base.setdefault(row.ref.split("#", 1)[0], []).append(row)
    start = opening
    group: list[ParsedRow] = []
    for ref in doc.data_refs:
        if ref in skipped and (figure := _carried(doc, ref)) is not None:
            if group:
                _settle(doc, group, start, figure, result)
            start, group = figure, []
            continue
        for row in by_base.get(ref, []):
            group.append(row)
            if row.balance_after_pence is not None:
                _settle(doc, group, start, row.balance_after_pence, result)
                start, group = row.balance_after_pence, []
    if group:
        _settle(doc, group, start, closing, result)
    return result


def _unsigned(row: ParsedRow) -> bool:
    return not row.edited and row.amount_pence != 0 and not _has_printed_sign(row.amount_text)


def _named(rows: Sequence[ParsedRow]) -> str:
    refs = [row.ref for row in rows]
    return ", ".join(refs) if len(refs) <= 3 else f"{refs[0]}, {refs[1]} and {len(refs) - 2} more"


def _cant_tell(rows: Sequence[ParsedRow], why: str) -> str:
    these = "this is" if len(rows) == 1 else "these are"
    return f"{_named(rows)}: can't tell whether {these} money in or out, because {why}"


def _ways(sizes: Sequence[int], target: int) -> int:
    """How many choices of sign for `sizes` add up to `target` (within a penny): 0, 1, or 2
    meaning more than one."""
    ways: dict[int, int] = {0: 1}
    for size in sizes:
        following: dict[int, int] = {}
        for total, count in ways.items():
            for value in (total + size, total - size):
                following[value] = min(2, following.get(value, 0) + count)
        ways = following
    return min(2, sum(ways.get(target + d, 0) for d in (-1, 0, 1)))


def _settle(
    doc: Document,
    rows: list[ParsedRow],
    before: int | None,
    after: int | None,
    result: SignRepair,
) -> None:
    unsigned = [row for row in rows if _unsigned(row)]
    if not unsigned:
        return
    it = "it" if len(unsigned) == 1 else "them"
    if before is None:
        result.errors.append(
            _cant_tell(unsigned, f"the balance before {it} isn't printed or couldn't be read")
        )
        return
    if after is None:
        result.errors.append(_cant_tell(unsigned, f"no balance is printed after {it}"))
        return
    fixed = sum(row.amount_pence for row in rows if not _unsigned(row))
    target = after - before - fixed
    if len(rows) == 1:  # between two printed balances: the difference is the amount
        row = rows[0]
        if target != row.amount_pence and abs(target) == abs(row.amount_pence):
            row.amount_pence = target
            row.sign_from = _label_for(target > 0, row, doc.lines)
            result.repaired.append(row.ref)
        return
    if len(unsigned) > MAX_UNSIGNED:
        result.errors.append(
            _cant_tell(unsigned, "too many rows share one printed balance to work it out")
        )
    elif _ways([abs(row.amount_pence) for row in unsigned], target) > 1:
        result.errors.append(
            _cant_tell(unsigned, f"the balance after {it} adds up more than one way")
        )
