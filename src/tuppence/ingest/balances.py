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
from tuppence.ingest.sensitive import is_figure_line
from tuppence.ingest.textnum import parse_money, to_pence

_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DATE = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})[a-z]*\.?(?:\s+\d{{2,4}})?\b"
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)
_OPENING = r"(?:opening|previous|starting|start)\s+balance"
_CLOSING = r"(?:closing|new|ending|end)\s+balance"
_LABEL = re.compile(
    rf"\b(?P<opening>{_OPENING})\b|\b(?P<closing>{_CLOSING})\b"
    r"|\b(?P<brought>(?:balance\s+)?(?:brought\s+forward|b/f\b)|balance\s+forward)\b"
    r"|\b(?P<carried>(?:balance\s+)?(?:carried\s+forward|c/f\b))",
    re.IGNORECASE,
)
# A printed figure: pence required, thousands separator optional, a GBP code before or after.
# Every optional piece takes the spaces after it, so a long gap can't make matching slow.
_MONEY = re.compile(
    r"(?:\(\s*)?(?:[+\-−–]\s*)?(?:(?:£|\$|€|GBP\b)\s*)?(?:[+\-−–]\s*)?"
    r"(?<![\d,.])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d.])"
    r"(?:\s*\))?(?:\s*GBP\b)?(?:\s*[\-−–])?(?:\s*(?:CR|DR)\b)?",
    re.IGNORECASE,
)
_CODE = re.compile(r"\bGBP\b", re.IGNORECASE)
_FILLER = re.compile(r"^(?:\s|:|\.|-|\(|\)|on|at|as|is|of|date)*$", re.IGNORECASE)
_MARKER = re.compile(r"\b(CR|DR)\b", re.IGNORECASE)
_CARRIED = re.compile(
    r"(?:brought|carried)\s+forward|\b[bc]/f\b|balance\s+forward"
    r"|(?:opening|start(?:ing)?|previous)\s+balance",
    re.IGNORECASE,
)
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
    value = parse_money(_CODE.sub("", token).strip())
    if value is None:
        return None
    marker = _MARKER.search(token)
    kind = marker.group(1).upper() if marker else ""
    if perspective == "card":
        magnitude = abs(value)
        negative = kind == "CR" or (value < 0 and kind != "DR")
        return to_pence(-magnitude if negative else magnitude)
    return to_pence(value)


# What may follow a balance figure: a marker saying which side of zero it is on, then the end
# of the line, a date word or the next summary label. Anything else leaves the figure unread.
_AFTER_FILL = re.compile(r"^[\s.,;:|()*]+")  # a footnote mark ("1,000.00*") is filler
_AFTER_MARKER = re.compile(
    r"^(?:(?P<overdrawn>O/D|OD|DR|D|overdrawn)|(?P<credit>CR|in\s+credit))(?![A-Za-z])\.?",
    re.IGNORECASE,
)
_AFTER_REST = re.compile(
    r"^(?:[\s.,;:|()]|\b(?:on|at|as|of|date)\b)*"
    r"(?:$|(?:money|paid|payments?|totals?|withdrawals|deposits|credits|debits|interest"
    r"|charges|fees|minimum|credit\s+limit|available|arranged|overdraft|statement|page|sort"
    r"|account|balance|opening|closing|new|previous|in|out)\b)",
    re.IGNORECASE,
)


def _signed(token: str, after: str, perspective: Perspective) -> int | None:
    """The figure `token` with any marker printed after it ("100.00 D", "100.00 OD",
    "100.00 overdrawn", "100.00 in credit"), or None when what follows isn't understood."""
    after = _AFTER_FILL.sub("", after)
    marker = _AFTER_MARKER.match(after)
    rest = after[marker.end() :] if marker else after
    if _AFTER_REST.match(rest) is None:
        return None
    pence = _balance_pence(token, perspective)
    if pence is None or marker is None:
        return pence
    if perspective == "card":  # a card prints owed (DR) or in credit (CR), never "overdrawn"
        if marker.group("credit"):
            return -abs(pence)
        return abs(pence) if marker.group("overdrawn").upper() == "DR" else None
    return -abs(pence) if marker.group("overdrawn") else abs(pence)


def _figure(tail: str, perspective: Perspective) -> int | None:
    """The one amount that follows a label, or None when anything else is in the way."""
    plain = _DATE.sub(" ", tail)
    match = _MONEY.search(plain)
    if match is None or not _FILLER.match(plain[: match.start()]):
        return None
    return _signed(match.group(0), plain[match.end() :], perspective)


def _below(doc: Document, ref: str, withheld: set[str]) -> str | None:
    """The withheld line just below `ref` when it is only a figure: the label's figure printed
    on the next line ("Opening balance" over "£1,000.00")."""
    at = next((i for i, line in enumerate(doc.lines) if line.ref == ref), None)
    if at is None or at + 1 >= len(doc.lines):
        return None
    below = doc.lines[at + 1]
    return below.text if below.ref in withheld and is_figure_line(below.text) else None


def local_balances(doc: Document, *, perspective: Perspective) -> LocalBalances:
    """Opening and closing balance from the withheld lines (the summary box, and the balance
    lines withheld from the table). A figure that appears twice with different values, or that
    isn't plainly labelled, is left out. A label with nothing after it but a date takes the
    figure-only line below it.

    "Balance brought forward" and "carried forward" are the balance at a point in the table.
    Above every row (in the summary box, or atop the table) brought forward is the opening
    balance and carried forward the closing one; below every row, carried forward is the closing
    balance. They count only when the statement prints no opening or closing balance of its own;
    the ones between pages are neither (sign repair reads those)."""
    by_ref = doc.by_ref()
    withheld = set(doc.preamble_refs)
    order = {line.ref: i for i, line in enumerate(doc.lines)}
    rows = [order[r] for r in doc.data_refs if r in order]
    first_row, last_row = (min(rows), max(rows)) if rows else (None, None)
    found: dict[str, set[int]] = {"opening": set(), "closing": set()}
    forward: dict[str, set[int]] = {"opening": set(), "closing": set()}
    unclear: set[str] = set()
    for ref in doc.preamble_refs:
        line = by_ref.get(ref)
        if line is None:
            continue
        labels = list(_LABEL.finditer(line.text))
        for i, label in enumerate(labels):
            end = labels[i + 1].start() if i + 1 < len(labels) else len(line.text)
            tail = line.text[label.end() : end]
            if end == len(line.text) and _FILLER.match(_DATE.sub(" ", tail)):
                tail = f"{tail} {_below(doc, ref, withheld) or ''}"
            pence = _figure(tail, perspective)
            if label.group("brought") or label.group("carried"):
                at = order.get(ref, 0)
                above = first_row is None or at < first_row  # the summary box, or atop the table
                below = last_row is not None and at > last_row
                if label.group("brought") and above:
                    side = "opening"
                elif label.group("carried") and (above or below):
                    side = "closing"
                else:
                    continue  # between rows: the balance at a page break, not an end
                if pence is not None:
                    forward[side].add(pence)
                continue
            side = "closing" if label.group("closing") else "opening"
            if pence is None:
                unclear.add(side)
            else:
                found[side].add(pence)
    return LocalBalances(
        opening=_only(found["opening"] or forward["opening"], "opening" in unclear),
        closing=_only(found["closing"] or forward["closing"], "closing" in unclear),
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
    figures = list(_MONEY.finditer(plain))
    if not figures:
        return None
    last = figures[-1]
    return _signed(last.group(0), plain[last.end() :], "household")


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
    # The rows in file order, with the balance lines withheld from among them (brought or
    # carried forward, a dated opening balance): each is a printed balance between rows.
    data = set(doc.data_refs)
    lines = [ln.ref for ln in doc.lines]
    first = next((i for i, ref in enumerate(lines) if ref in data), len(lines))
    withheld = set(doc.preamble_refs)
    between = {ref for ref in lines[first:] if ref in withheld}
    start = opening
    group: list[ParsedRow] = []
    for ref in lines[first:]:
        if ref in between:
            if (figure := _carried(doc, ref)) is not None:
                if group:
                    _settle(doc, group, start, figure, result)
                start, group = figure, []
            continue
        if ref not in data:
            continue
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
