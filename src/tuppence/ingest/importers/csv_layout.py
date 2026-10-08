"""Read a CSV or XLSX table with a known layout. No AI involved."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from pydantic import BaseModel, Field

from tuppence.core.errors import UserFacing
from tuppence.ingest.clock import Deadline, ticking
from tuppence.ingest.models import Document, ParsedRow, ParsedStatement, SkippedLine
from tuppence.ingest.registry import CsvLayout, column_getter, data_records, header_cells, norm
from tuppence.ingest.textnum import (
    direction_of,
    has_credit_marker,
    has_printed_sign,
    parse_date,
    parse_money,
    to_pence,
)

Getter = Callable[[Sequence[str], str | None], str]


class ImportResult(BaseModel):
    parsed: ParsedStatement
    problems: list[str] = Field(default_factory=list)  # reported like check errors
    last4: str | None = None


class LayoutMismatch(UserFacing, ValueError):
    """The file lacks a column the layout needs."""


def parse_with_layout(
    doc: Document, layout: CsvLayout, *, deadline: Deadline | None = None
) -> ImportResult:
    header = header_cells(doc)
    _require_columns(layout, header)
    get = column_getter(layout, header)
    records: list[tuple[list[ParsedRow], list[SkippedLine]]] = []
    problems: list[str] = []
    last4: str | None = None
    tick = ticking(deadline)
    for n, (ref, cells) in enumerate(data_records(doc)):
        tick(n)
        rows, skips, problem = _record(ref, cells, layout, get)
        records.append((rows, skips))
        if problem:
            problems.append(problem)
        if last4 is None and layout.account_number:
            digits = re.sub(r"\D", "", get(cells, layout.account_number))
            last4 = digits[-4:] if len(digits) >= 4 else None
    dated = [rows[-1].date for rows, _ in records if rows]
    if len(dated) >= 2 and dated[0] > dated[-1]:
        records.reverse()  # the file lists newest first; keep rows oldest first
    rows = [row for record_rows, _ in records for row in record_rows]
    parsed = ParsedStatement(
        importer=f"csv:{layout.id}",
        perspective=layout.perspective,
        rows=rows,
        skipped=[skip for _, record_skips in records for skip in record_skips],
        period_start=min((r.date for r in rows), default=None),
        period_end=max((r.date for r in rows), default=None),
    )
    parsed.opening_balance_pence, parsed.closing_balance_pence = _balances(parsed)
    return ImportResult(parsed=parsed, problems=problems, last4=last4)


def _require_columns(layout: CsvLayout, header: Sequence[str] | None) -> None:
    wanted = [
        layout.date,
        *layout.description,
        layout.amount,
        layout.money_out,
        layout.money_in,
        layout.balance,
        layout.merchant,
        layout.category,
        layout.type,
        layout.direction,
        layout.account_number,
        layout.fee,
        *(rule.column for rule in layout.skip),
    ]
    width = len(header) if header is not None else (layout.columns or 0)
    present = {norm(c) for c in header or []}
    for ref in filter(None, wanted):
        ok = int(ref[1:]) < width if ref.startswith("#") else norm(ref) in present
        if not ok:
            raise LayoutMismatch(
                f'The "{layout.name}" layout needs a "{ref}" column and this file has none.'
            )


def _record(
    ref: str, cells: Sequence[str], layout: CsvLayout, get: Getter
) -> tuple[list[ParsedRow], list[SkippedLine], str | None]:
    for rule in layout.skip:
        value = get(cells, rule.column)
        if (rule.not_in is not None and value not in rule.not_in) or (
            rule.equals is not None and value in rule.equals
        ):
            return [], [SkippedLine(ref=ref, reason=rule.reason)], None
    raw_date = get(cells, layout.date)
    day = parse_date(raw_date, layout.date_formats)
    if day is None:
        return [], [], f'{ref}: can\'t read the date "{raw_date}"'
    sign_from: str | None = None
    if layout.amount is not None:
        amount_text = get(cells, layout.amount)
        value = parse_money(amount_text)
        if value is None:
            if not amount_text:
                return [], [SkippedLine(ref=ref, reason="no amount on this line")], None
            return [], [], f'{ref}: can\'t read the amount "{amount_text}"'
        pence = to_pence(value)
        if layout.direction is not None:  # a DR/CR column says which way every amount goes
            marker = get(cells, layout.direction)
            way = direction_of(marker)
            if way is None:
                return [], [], f"{ref}: can't tell whether the amount is money in or out"
            if has_printed_sign(amount_text) and pence * way < 0:
                return [], [], f"{ref}: the amount's sign and its DR/CR marker disagree"
            pence, sign_from = way * abs(pence), marker
        elif layout.perspective == "card":
            pence = abs(pence) if has_credit_marker(amount_text) else -pence
    else:
        assert layout.money_out is not None and layout.money_in is not None
        out_text, in_text = get(cells, layout.money_out), get(cells, layout.money_in)
        out_value, in_value = parse_money(out_text), parse_money(in_text)
        if out_value and in_value:
            return [], [], f"{ref}: both money out and money in are filled in"
        if out_value:
            amount_text, pence, sign_from = out_text, -abs(to_pence(out_value)), layout.money_out
        elif in_value:
            amount_text, pence, sign_from = in_text, abs(to_pence(in_value)), layout.money_in
        else:
            return [], [SkippedLine(ref=ref, reason="no amount on this line")], None
    if pence == 0:
        return [], [SkippedLine(ref=ref, reason="zero-amount line (a card check or a hold)")], None
    description = (
        " ".join(v for c in layout.description if (v := get(cells, c))) or "(no description)"
    )
    balance = parse_money(get(cells, layout.balance))
    row = ParsedRow(
        ref=ref,
        date=day,
        amount_pence=pence,
        amount_text=amount_text,
        sign_from=sign_from,
        raw_description=description,
        merchant=get(cells, layout.merchant) or None,
        bank_category=get(cells, layout.category) or None,
        bank_type=get(cells, layout.type) or None,
        balance_after_pence=to_pence(balance) if balance is not None else None,
    )
    fee_text = get(cells, layout.fee)
    fee = parse_money(fee_text)
    if not fee:
        return [row], [], None
    fee_row = ParsedRow(
        ref=f"{ref}#fee",
        date=day,
        amount_pence=-abs(to_pence(fee)),
        amount_text=fee_text,
        raw_description=f"{description} (fee)",
        merchant=row.merchant,
        bank_type="fee",
    )
    return [fee_row, row], [], None  # the fee first: the printed balance already includes it


def _balances(parsed: ParsedStatement) -> tuple[int | None, int | None]:
    """Opening and closing balances implied by a per-row Balance column."""

    def delta(row: ParsedRow) -> int:
        return -row.amount_pence if parsed.perspective == "card" else row.amount_pence

    opening = closing = None
    running = 0
    for row in parsed.rows:
        running += delta(row)
        if row.balance_after_pence is not None:
            opening = row.balance_after_pence - running
            break
    after = 0
    for row in reversed(parsed.rows):
        if row.balance_after_pence is not None:
            closing = row.balance_after_pence + after
            break
        after += delta(row)
    return opening, closing
