"""Verify parsed rows against the statement text (ported from v3, the predecessor app).

Whoever produced the rows (a fixed importer, a learned CSV mapping or the AI
reader), this module checks that each row is still visible on the line it cites:
the amount is printed there and its size matches, the sign follows the printed
convention, every data line is used exactly once, dates sit inside the period,
and the balances add up. Amounts are stored from the household's perspective;
`perspective="card"` means the file prints purchases as positive figures.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from tuppence.core.money import MAX_PENCE
from tuppence.ingest.models import (
    CheckLevel,
    Document,
    Line,
    ParsedRow,
    ParsedStatement,
    Perspective,
    SkippedLine,
)
from tuppence.ingest.textnum import direction_of, pounds, to_pence

MAX_PERIOD_DAYS = 400
DATE_SLACK = timedelta(days=3)
_PAGE = re.compile(r"P(\d+)L\d+")
_LINE_REF = re.compile(r"L(\d+)")
# Matched right after a figure; a run of separators is tried from its start only.
_CREDIT_MARKER = re.compile(r"(?<![\s,;|])[\s,;|]*CR\b", re.IGNORECASE)
_DEBIT_MARKER = re.compile(r"(?<![\s,;|])[\s,;|]*DR\b", re.IGNORECASE)


def base_ref(ref: str) -> str:
    """`L12#fee` → `L12`: one line may give more than one row."""
    return ref.split("#", 1)[0]


# --- figures: printed money tokens, read one way for amounts, balances and signs ----------------

# A run of digits with its thousands commas and decimal points, taken whole: "1,250.00" is one
# run, so "250.00" inside it is never a figure of its own; "50.00.2026" (two points) is none.
_RUN = re.compile(r"\d[\d,]*(?:\.\d+)*")
_CELL_EDGE = ',;\t|"'


@dataclass(frozen=True)
class Figure:
    """A money figure printed on a line: where its digits are, its size in pence, and what is
    printed around it."""

    start: int  # first digit
    end: int  # after the last digit
    pence: int  # the size, never negative
    negative: bool  # a minus before it (after any £), brackets round it, or a minus after it
    shown: str  # as printed, with its sign or brackets


def _grouped_start(whole: str) -> int:
    """Where the valid number starts in `whole` (digits and commas): the longest tail that is
    a lone digit run or a 1-3 digit head followed by groups of exactly three ("2026,12" is the
    year and then 12; "1,250" is one number)."""
    groups = whole.split(",")
    offset = 0
    for k, head in enumerate(groups):
        rest = groups[k + 1 :]
        if head and (not rest or (len(head) <= 3 and all(len(g) == 3 for g in rest))):
            return offset
        offset += len(head) + 1
    return len(whole)


def _at_cell_edge(text: str, start: int, end: int) -> bool:
    """The number is a whole cell of a CSV line, allowing a sign, £ or brackets round it."""
    before = text[:start].rstrip(" £$€-+\u2212\u2013(")
    after = text[end:].lstrip(" )-\u2212")
    return (not before or before[-1] in _CELL_EDGE) and (not after or after[0] in _CELL_EDGE)


def figures(text: str) -> list[Figure]:
    """Every money figure on `text`, in order: a number with pence ("1,250.00"), or a whole
    number after a currency sign ("£250") or alone in a cell of a CSV line (",900,"). Only
    whole runs count, so no figure is ever part of a longer number or a date."""
    found: list[Figure] = []
    for match in _RUN.finditer(text):
        run = match.group(0).rstrip(",")
        if run.count(".") > 1:
            continue
        whole, _, pennies = run.partition(".")
        if "." in run and len(pennies) != 2:
            continue
        offset = _grouped_start(whole)
        if offset >= len(whole):
            continue
        start, end = match.start() + offset, match.start() + len(run)
        if not pennies and not (
            (start > 0 and text[start - 1] in "£$€") or _at_cell_edge(text, start, end)
        ):
            continue
        number = whole[offset:].replace(",", "") + (f".{pennies}" if pennies else "")
        cursor = start - 1 if start > 0 and text[start - 1] in "£$€" else start
        lead = text[cursor - 1] if cursor > 0 else ""
        bracketed = lead == "(" and text[end : end + 1] == ")"
        trailing = text[end : end + 1] in ("-", "\u2212") and not text[end + 1 : end + 2].isalnum()
        if lead and lead in "-\u2212\u2013":
            negative, shown = True, text[cursor - 1 : end]
        elif bracketed:
            negative, shown = True, text[cursor - 1 : end + 1]
        elif trailing:
            negative, shown = True, text[cursor : end + 1]
        else:
            negative, shown = False, text[cursor:end]
        found.append(Figure(start, end, to_pence(Decimal(number)), negative, shown))
    return found


def amount_renderings(pence: int) -> set[str]:
    """Ways an absolute amount may be written, with and without thousands separators."""
    whole, frac = divmod(abs(pence), 100)
    two_dp = f"{whole}.{frac:02d}"
    if frac == 0:
        short = str(whole)
    elif frac % 10 == 0:
        short = f"{whole}.{frac // 10}"
    else:
        short = two_dp
    return {short, two_dp, _grouped(short), _grouped(two_dp)}


# Matched right after a figure; a run of spaces is tried from its start only.
_OVERDRAWN_AFTER = re.compile(r"(?<!\s)\s*(?:O/D|OD|DR|D|overdrawn)\b", re.IGNORECASE)
_CREDIT_AFTER = re.compile(r"(?<!\s)\s*(?:CR|in\s+credit)\b", re.IGNORECASE)


def balance_printed(row: ParsedRow, text: str, perspective: Perspective) -> bool:
    """Whether the running balance a row claims is printed on its own line `text` (the line as
    it was sent) as that line's balance: the last figure on it, after the row's amount (so the
    amount itself never counts), the same size and with the sign it is stored with. A row that
    claims no balance passes.

    A balance the model worked out for itself, printed nowhere, is not evidence of anything:
    it can't prove which way an amount goes or that a statement adds up."""
    balance = row.balance_after_pence
    if balance is None:
        return True
    printed = figures(text)
    if len(printed) < 2:  # a single figure is the row's own amount
        return False
    last = printed[-1]  # the balance column is the last on the line
    after = text[last.end :]
    if perspective == "card":  # a card prints what is owed; CR means in credit
        negative = last.negative or _CREDIT_AFTER.match(after) is not None
    else:
        negative = last.negative or _OVERDRAWN_AFTER.match(after) is not None
    return last.pence == abs(balance) and negative == (balance < 0)


def drop_unprinted_balances(parsed: ParsedStatement, lines: Sequence[Line]) -> list[str]:
    """Clear every running balance the model reported that isn't printed on its row's line
    (`balance_printed`); returns the refs of the rows changed."""
    by_ref = {line.ref: line for line in lines}
    dropped: list[str] = []
    for row in parsed.rows:
        line = by_ref.get(base_ref(row.ref))
        if row.balance_after_pence is None:
            continue
        if line is None or not balance_printed(row, line.text, parsed.perspective):
            row.balance_after_pence = None
            dropped.append(row.ref)
    return dropped


def check_rows(
    chunk: Sequence[Line],
    *,
    all_lines: Sequence[Line],
    context_refs: Sequence[str],
    data_refs: Sequence[str],
    parsed: ParsedStatement,
    level: CheckLevel = "full",
) -> list[str]:
    lines = _combine(all_lines, chunk)
    if level == "screenshot":
        return _too_large(parsed.rows) + _zero(parsed.rows) + _evidence_only(parsed.rows, lines)
    errors = _too_large(parsed.rows) + _zero(parsed.rows)
    errors.extend(_coverage(parsed.rows, parsed.skipped, context_refs, data_refs))
    period_errors = _period(parsed)
    errors.extend(_rows(parsed, lines, context_refs))
    errors.extend(_skipped(parsed.skipped))
    errors.extend(_running(parsed.rows, None, parsed.perspective))
    errors.extend(period_errors)
    return errors


def check_statement(
    parsed: ParsedStatement, *, level: CheckLevel = "full", dates: bool = False
) -> list[str]:
    """Opening plus every amount against closing, and running balances.

    A household-perspective file: opening + Σ amounts = closing. A card file prints
    what is owed, so opening − Σ (household amounts) = closing:
    previous + purchases − payments + interest. With `dates`, also every row's date
    against the period (used after the AI read, once the header's period is known).
    """
    if level == "screenshot":
        return []
    errors: list[str] = []
    start, end = parsed.period_start, parsed.period_end
    if dates and start is not None and end is not None:
        for row in parsed.rows:
            if not row.edited and not start - DATE_SLACK <= row.date <= end + DATE_SLACK:
                errors.append(_outside(row, start, end))
    opening, closing = parsed.opening_balance_pence, parsed.closing_balance_pence
    if opening is not None and closing is not None:
        signed = sum(row.amount_pence for row in parsed.rows)
        total, joined = (
            (opening - signed, "-") if parsed.perspective == "card" else (opening + signed, "+")
        )
        if abs(total - closing) > 1:
            errors.append(
                f"balance mismatch: opening {pounds(opening)} {joined} sum {pounds(signed)} = "
                f"{pounds(total)}, closing {pounds(closing)}"
            )
    errors.extend(_running(parsed.rows, opening, parsed.perspective))
    return errors


def check_document(
    doc: Document, parsed: ParsedStatement, *, level: CheckLevel = "full"
) -> list[str]:
    """Every check over a whole file at once (fixed importers and learned CSV mappings)."""
    errors = check_rows(
        doc.lines,
        all_lines=doc.lines,
        context_refs=doc.header_refs,
        data_refs=doc.data_refs,
        parsed=parsed,
        level=level,
    )
    return list(
        dict.fromkeys(errors + check_statement(parsed, level=level))
    )  # running-balance errors appear in both


def balance_verified(parsed: ParsedStatement, errors: Sequence[str], level: CheckLevel) -> bool:
    """ "Balances add up": a full statement with rows, an opening and a closing balance, no
    check failing, and the sums done here again (opening plus every amount gives the closing
    balance, and every running balance follows). Nothing else counts: not a flag or a field
    from the reader, nor `errors` that may not have included the sums. With no balance
    printed there is nothing to verify, which is not the same as adding up."""
    return (
        level == "full"
        and not errors
        and bool(parsed.rows)
        and parsed.opening_balance_pence is not None
        and parsed.closing_balance_pence is not None
        and not check_statement(parsed, level=level)
    )


# --- helpers -------------------------------------------------------------------------------


def _too_large(rows: Sequence[ParsedRow]) -> list[str]:
    return [
        f"{row.ref}: amount {pounds(row.amount_pence)} is larger than any real transaction"
        for row in rows
        if abs(row.amount_pence) > MAX_PENCE
    ]


def _zero(rows: Sequence[ParsedRow]) -> list[str]:
    return [f"{row.ref}: amount is zero" for row in rows if row.amount_pence == 0]


def _grouped(text: str) -> str:
    if "." in text:
        whole, frac = text.split(".", 1)
        return f"{int(whole):,}.{frac}"
    return f"{int(text):,}"


def _combine(whole: Sequence[Line], chunk: Sequence[Line]) -> list[Line]:
    seen: set[tuple[str, str]] = set()
    out: list[Line] = []
    for line in [*whole, *chunk]:
        key = (line.ref, line.text)
        if key not in seen:
            seen.add(key)
            out.append(line)
    return out


def _coverage(
    rows: Sequence[ParsedRow],
    skipped: Sequence[SkippedLine],
    context_refs: Sequence[str],
    data_refs: Sequence[str],
) -> list[str]:
    context, data = set(context_refs), set(data_refs)
    counts: dict[str, int] = {}
    order: list[str] = []
    covered: set[str] = set()
    for ref in [r.ref for r in rows] + [s.ref for s in skipped]:
        if not ref:
            continue
        if ref not in counts:
            order.append(ref)
        counts[ref] = counts.get(ref, 0) + 1
        covered.add(base_ref(ref))
    errors: list[str] = []
    for ref in order:
        if counts[ref] > 1:
            errors.append(f"{ref}: duplicate ref")
        base = base_ref(ref)
        if base in context:
            errors.append(f"{ref}: header line included")
        elif base not in data:
            errors.append(f"unexpected ref: {ref}")
    missing = [ref for ref in data_refs if ref not in covered]
    if missing:
        errors.append(f"missing refs: {_compress(missing)}")
    return errors


def _compress(refs: Sequence[str]) -> str:
    numbers: list[int] = []
    others: list[str] = []
    for ref in refs:
        match = _LINE_REF.fullmatch(ref)
        if match:
            numbers.append(int(match.group(1)))
        else:
            others.append(ref)
    numbers.sort()
    parts: list[str] = []
    i = 0
    while i < len(numbers):
        start = end = numbers[i]
        while i + 1 < len(numbers) and numbers[i + 1] == end + 1:
            i += 1
            end = numbers[i]
        parts.append(f"L{start}" if start == end else f"L{start}-L{end}")
        i += 1
    parts.extend(others)
    return ", ".join(parts)


def _outside(row: ParsedRow, start: date, end: date) -> str:
    period = f"{start.isoformat()}..{end.isoformat()}"
    return f"{row.ref}: date {row.date.isoformat()} outside {period} (±3 days)"


def _period(parsed: ParsedStatement) -> list[str]:
    # The period is what the statement declares. It may be wider than the
    # transactions, so it isn't compared with the dates found in the file.
    start, end = parsed.period_start, parsed.period_end
    errors: list[str] = []
    if start is None:
        errors.append("statement period_start missing")
    if end is None:
        errors.append("statement period_end missing")
    if start is None or end is None:
        return errors
    if start > end:
        errors.append(f"period_start {start.isoformat()} is after period_end {end.isoformat()}")
    elif (end - start).days > MAX_PERIOD_DAYS:
        errors.append(
            f"period {start.isoformat()}..{end.isoformat()} is longer than {MAX_PERIOD_DAYS} days"
        )
    return errors


def _evidence(row: ParsedRow, line: Line) -> list[str]:
    """`amount_text` is one figure (read by `figures`, like every figure here), its size is the
    row's amount, and it is printed on the line as a whole figure: "50.00" is not found in
    "150.00" or "£1,250.00"."""
    errors: list[str] = []
    own = figures(row.amount_text)
    if len(own) != 1:
        errors.append(f'{row.ref}: amount_text "{row.amount_text}" is not a number')
        return errors
    figure = own[0]
    if figure.pence != abs(row.amount_pence):
        errors.append(
            f'{row.ref}: amount_text "{row.amount_text}" is {pounds(figure.pence)}, '
            f"amount is {pounds(row.amount_pence)}"
        )
    printed = {(f.start, f.end) for f in figures(line.text)}
    start = 0
    while (index := line.text.find(row.amount_text, start)) >= 0:
        if (index + figure.start, index + figure.end) in printed:
            return errors
        start = index + 1
    errors.append(f'{row.ref}: amount_text "{row.amount_text}" not found on line')
    return errors


def _evidence_only(rows: Sequence[ParsedRow], lines: Sequence[Line]) -> list[str]:
    by_ref = {line.ref: line for line in lines}
    errors: list[str] = []
    for row in rows:
        if row.edited:
            continue
        line = by_ref.get(base_ref(row.ref))
        if line is None:
            errors.append(f"{row.ref}: source line not found")
            continue
        errors.extend(_evidence(row, line))
    return errors


def _rows(parsed: ParsedStatement, lines: Sequence[Line], context_refs: Sequence[str]) -> list[str]:
    by_ref: dict[str, Line] = {}
    for line in lines:
        by_ref.setdefault(line.ref, line)
    context_set = set(context_refs)
    context_text = "\n".join(line.text for line in lines if line.ref in context_set)
    page_cache: dict[str, str] = {}
    start, end = parsed.period_start, parsed.period_end
    errors: list[str] = []
    for row in parsed.rows:
        if row.edited:
            continue
        line = by_ref.get(base_ref(row.ref))
        if line is None:
            errors.append(f"{row.ref}: source line not found")
            continue
        errors.extend(_evidence(row, line))
        label = (row.sign_from or "").strip()
        # Only a CSV importer's own split-out fee rows ("L12#fee", from a Fee column) are
        # printed as a plain charge. A ref the AI reader returns never gets this.
        fee = row.ref.endswith("#fee") and parsed.importer.startswith("csv:")
        # A card's figure is read the card's way unless a DR/CR cell on the row gives its sign.
        if parsed.perspective == "card" and _cell_direction(label, line.text) is None:
            errors.extend(_card_sign(row, line.text))
        else:
            errors.extend(_sign(row, line.text, label, fee=fee))
        page = _PAGE.fullmatch(base_ref(row.ref))
        if page and page.group(1) not in page_cache:
            page_cache[page.group(1)] = _page_text(row.ref, lines)
        where = page_cache[page.group(1)] if page else context_text
        errors.extend(
            _sign_from(
                row,
                label,
                where,
                "page" if _PAGE.fullmatch(base_ref(row.ref)) else "header",
                line.text,
            )
        )
        if (
            start is not None
            and end is not None
            and start <= end
            and not (start - DATE_SLACK <= row.date <= end + DATE_SLACK)
        ):
            errors.append(_outside(row, start, end))
    return errors


def _sign(row: ParsedRow, text: str, sign_from: str, *, fee: bool = False) -> list[str]:
    amount = row.amount_pence
    found = _occurrences(text, amount, debit_is_negative=True)
    negatives = [shown for negative, shown in found if negative]
    positives = [shown for negative, shown in found if not negative]
    if negatives and amount >= 0:
        return [f"{row.ref}: sign mismatch (line shows {negatives[0]}, amount is {pounds(amount)})"]
    # A row merely typed "fee" (by a CSV Type column or the reader) is still sign-checked.
    if positives and not negatives and amount <= 0 and not sign_from and not fee:
        return [f"{row.ref}: sign mismatch (line shows {positives[0]}, amount is {pounds(amount)})"]
    if "-" in row.amount_text and amount >= 0 and not negatives:
        return [
            f'{row.ref}: sign mismatch (amount_text "{row.amount_text}" has a minus, '
            f"amount is {pounds(amount)})"
        ]
    return []


def _card_sign(row: ParsedRow, text: str) -> list[str]:
    # A card file prints the card's view: a purchase is a plain figure (stored
    # negative), a payment or refund has a minus or CR (stored positive).
    amount = row.amount_pence
    credit = _credit_showing(text, amount)
    if credit:
        if amount <= 0:
            return [
                f"{row.ref}: sign mismatch (card statement shows {credit}, "
                f"amount is {pounds(amount)})"
            ]
        return []
    found = _occurrences(text, amount, debit_is_negative=False)
    negatives = [shown for negative, shown in found if negative]
    positives = [shown for negative, shown in found if not negative]
    if negatives and amount <= 0:
        return [
            f"{row.ref}: sign mismatch (card statement shows {negatives[0]}, "
            f"amount is {pounds(amount)})"
        ]
    if positives and not negatives and amount >= 0:
        return [
            f"{row.ref}: sign mismatch (card statement shows {positives[0]}, "
            f"amount is {pounds(amount)})"
        ]
    if "-" in row.amount_text and amount <= 0 and not negatives:
        return [
            f'{row.ref}: sign mismatch (amount_text "{row.amount_text}" has a minus, '
            f"amount is {pounds(amount)})"
        ]
    return []


def _credit_showing(text: str, pence: int) -> str | None:
    """The printed figure when this amount is followed by CR, else None."""
    for figure in figures(text):
        if figure.pence == abs(pence) and (marker := _CREDIT_MARKER.match(text[figure.end :])):
            return text[figure.start : figure.end] + marker.group(0)
    return None


def _occurrences(text: str, pence: int, *, debit_is_negative: bool) -> list[tuple[bool, str]]:
    """(negative?, as printed) for each figure on the line the size of this amount.

    Negative means a leading minus, brackets, a trailing minus or, on a current account,
    DR. On a card, DR is a plain purchase, so it counts as printed positive.
    """
    found: list[tuple[bool, str]] = []
    for figure in figures(text):
        if figure.pence != abs(pence):
            continue
        debit = _DEBIT_MARKER.match(text[figure.end :])
        if not figure.negative and debit and debit_is_negative:
            found.append((True, text[figure.start : figure.end] + debit.group(0)))
        else:
            found.append((figure.negative, figure.shown))
    return found


def _cell_direction(label: str, own: str) -> int | None:
    """The direction of a DR/CR (Debit/Credit) marker printed as a whole cell of the row's own
    line (a table's direction column), else None."""
    cell = rf"(?:^|[,;\t|]) *\"?{re.escape(label.strip())}\"? *(?:[,;\t|]|$)"
    return direction_of(label) if re.search(cell, own, re.IGNORECASE) else None


def _sign_from(row: ParsedRow, label: str, where: str, place: str, own: str = "") -> list[str]:
    if not label:
        return []
    direction = _cell_direction(label, own)
    if direction is None:
        short = len(label) <= 3  # "DR" must not match inside "Address"
        present = (
            re.search(rf"\b{re.escape(label)}\b", where, re.IGNORECASE) is not None
            if short
            else label.casefold() in where.casefold()
        )
        if not present:
            return [f'{row.ref}: sign_from "{label}" not on {place}']
        direction = _label_sign(label)
    if direction is None:
        return [f'{row.ref}: sign_from "{label}" is not a sign label']
    if direction < 0 and row.amount_pence >= 0:
        return [
            f'{row.ref}: sign mismatch (sign_from "{label}" requires negative, '
            f"amount is {pounds(row.amount_pence)})"
        ]
    if direction > 0 and row.amount_pence <= 0:
        return [
            f'{row.ref}: sign mismatch (sign_from "{label}" requires positive, '
            f"amount is {pounds(row.amount_pence)})"
        ]
    return []


def _label_sign(label: str) -> int | None:
    text = label.casefold()
    rules = (
        (r"^\W*dr\W*$", -1),
        (r"^\W*cr\W*$", 1),
        (r"paid\s+out|money\s+out|withdrawal", -1),
        (r"paid\s+in|money\s+in|deposit", 1),
        (r"\bdebit\b|\bdbit\b", -1),
        (r"\bcredit\b|\bcrdt\b", 1),
        (r"\bout\b", -1),
        (r"\bin\b", 1),
    )
    for pattern, sign in rules:
        if re.search(pattern, text):
            return sign
    return None


def _page_text(ref: str, lines: Sequence[Line]) -> str:
    match = _PAGE.fullmatch(base_ref(ref))
    if not match:
        return ""
    page = match.group(1)
    return "\n".join(
        ln.text for ln in lines if (m := _PAGE.fullmatch(ln.ref)) and m.group(1) == page
    )


def _skipped(skipped: Sequence[SkippedLine]) -> list[str]:
    return [f"{s.ref}: skipped without reason" for s in skipped if not s.reason.strip()]


def _running(rows: Sequence[ParsedRow], opening: int | None, perspective: Perspective) -> list[str]:
    errors: list[str] = []
    anchor = opening
    for row in rows:
        delta = -row.amount_pence if perspective == "card" else row.amount_pence
        if anchor is None:
            if row.balance_after_pence is not None:
                anchor = row.balance_after_pence
            continue
        anchor += delta
        if row.balance_after_pence is None:
            continue
        if abs(anchor - row.balance_after_pence) > 1:
            errors.append(
                f"{row.ref}: running balance mismatch (previous {pounds(anchor - delta)} + amount "
                f"{pounds(delta)} = {pounds(anchor)}, got {pounds(row.balance_after_pence)})"
            )
            anchor = row.balance_after_pence
    return errors
