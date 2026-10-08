"""Learn an unfamiliar CSV layout: the AI assigns column roles once, the mapping is checked
like any importer, and a mapping that passes is saved so later files in that layout need no
AI call (spec §6.2 step 3).

The model never sees a value from the file. It gets a sketch of the file's shape: the column
headings (a heading that names account details is hidden, long digit runs are replaced) and,
for a few rows, one type token per cell such as <DATE:dd/mm/yyyy>, <AMOUNT:-12.30>,
<NUMBER:8 digits>, <TEXT> or <EMPTY>. The date format is worked out on this device from every
value in the column the model picks, and amounts are read with the importer's own money rules,
so the model only says which column is which. Feedback on a retry names columns and counts
problems; it never quotes a cell.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tuppence.core.errors import UserFacing, safe_error_text
from tuppence.ingest.check import check_document
from tuppence.ingest.importers.csv_layout import ImportResult, LayoutMismatch, parse_with_layout
from tuppence.ingest.models import AccountKind, Document, ParsedStatement
from tuppence.ingest.prompts import load_prompt
from tuppence.ingest.reader import StructuredLLM, tidy
from tuppence.ingest.registry import (
    CsvLayout,
    data_records,
    header_cells,
    learned_id,
    norm,
)
from tuppence.ingest.sensitive import HIDDEN, classify, holds_details, mask
from tuppence.ingest.textnum import direction_of, has_printed_sign
from tuppence.llm.types import LLMBadResponse, Message

SAMPLE_ROWS = 5
# Every format has a separator or a month name, so no run of bare digits (an account number,
# a sort code) can pass for a date.
ALLOWED_DATE_FORMATS = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %b %y",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y",
)
_FORMAT_NAMES = {
    "%d": "dd",
    "%m": "mm",
    "%Y": "yyyy",
    "%y": "yy",
    "%b": "mon",
    "%B": "month",
    "%H": "hh",
    "%M": "mm",
    "%S": "ss",
}


class MappingOut(BaseModel):
    """Column roles only. Formats are worked out on this device."""

    date_column: str
    description_columns: list[str]
    merchant_column: str | None
    amount_column: str | None
    money_out_column: str | None
    money_in_column: str | None
    amounts_are: Literal["money_out_negative", "purchases_positive"]
    balance_column: str | None
    category_column: str | None
    type_column: str | None


class MappingError(UserFacing, ValueError):
    """The proposed mapping can't be used. The text names columns and formats only, never a
    value from the file, so it can be shown to the user and sent back to the model."""


class MappingOutcome(BaseModel):
    layout: CsvLayout | None
    result: ImportResult | None
    errors: list[str] = Field(default_factory=list)
    attempts: int = 0


# --- the sketch -----------------------------------------------------------------------------

_SIGN = "+\\-−–"
# Each optional piece takes the spaces next to it, so a long gap can't make matching slow.
_AMOUNT = re.compile(
    rf"^\s*(?:(?P<open>\()\s*)?(?:(?P<lead>[{_SIGN}])\s*)?(?:(?P<cur>£|\$|€|GBP)\s*)?"
    rf"(?:(?P<lead2>[{_SIGN}])\s*)?"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{1,2}(?:\s*(?P<close>\)))?(?:\s*(?P<trail>[\-−]))?"
    r"(?:\s*(?P<marker>CR|DR))?\.?\s*$",
    re.IGNORECASE,
)
_NUMBERISH = re.compile(rf"^[{_SIGN}'\s]*\d[\d\s.,/'{_SIGN}]*$")


def _format_name(fmt: str) -> str:
    return re.sub(r"%[a-zA-Z]", lambda m: _FORMAT_NAMES.get(m.group(0), "?"), fmt).replace("T", " ")


def _date_format(cell: str) -> str | None:
    for fmt in ALLOWED_DATE_FORMATS:
        try:
            datetime.strptime(cell, fmt)
        except ValueError:
            continue
        return fmt
    return None


def cell_token(cell: str, *, names: Sequence[str] = ()) -> str:
    """What the model sees of one cell: its type and how it is written, never its value.
    A cell holding account details is HIDDEN outright."""
    text = " ".join(cell.split())
    if not text:
        return "<EMPTY>"
    if classify(text, names=names):
        return HIDDEN
    fmt = _date_format(text)
    if fmt is not None:
        return f"<DATE:{_format_name(fmt)}>"
    amount = _AMOUNT.match(text)
    if amount and bool(amount["open"]) == bool(amount["close"]):
        shown = f"{amount['cur'].strip().upper() if amount['cur'] else ''}12.30"
        if amount["open"]:
            shown = f"({shown})"
        sign = amount["lead"] or amount["lead2"]
        if sign:
            shown = ("+" if sign == "+" else "-") + shown
        if amount["trail"]:
            shown += "-"
        if amount["marker"]:
            shown += f" {amount['marker'].upper()}"
        return f"<AMOUNT:{shown}>"
    if _NUMBERISH.match(text):
        return f"<NUMBER:{sum(ch.isdigit() for ch in text)} digits>"
    return "<TEXT>"


def shown_heading(cell: str, *, names: Sequence[str] = ()) -> str:
    """A column heading as the model sees it: hidden when it names account details (a sort
    code or account number column, say), with any run of four or more digits replaced."""
    text = " ".join(cell.split())
    return mask(text, names=names) if text else ""


_HEADING_WORDS = re.compile(
    r"\b(?:date|posted|posting|description|details|narrative|memo|notes?|payee|merchant"
    r"|reference|ref|transaction|type|category|amount|value|balance|debit|credit|paid"
    r"|money|withdrawals?|deposits?|counter\s*party|currency|spend|received)\b",
    re.IGNORECASE,
)


def header_problem(header: Sequence[str], *, names: Sequence[str] = ()) -> str | None:
    """Why this row can't be the file's column headings, in plain words, or None.

    A real header has three or more headings, at least two of them recognisable column names,
    no account details, and no 'Label:' cells (a key/value preamble such as `Name,Alex Example`
    above the table)."""
    filled = [" ".join(c.split()) for c in header if c.strip()]
    if any(holds_details(c, names=names) for c in filled):
        return (
            "This file's column headings include account details, so its layout can't be "
            "worked out automatically."
        )
    if (
        len(filled) < 3
        or any(c.endswith(":") for c in filled)
        or sum(1 for c in filled if _HEADING_WORDS.search(c)) < 2
    ):
        return (
            "This file's first row doesn't look like column headings, so its layout can't be "
            "worked out automatically."
        )
    return None


def sketch(
    header: Sequence[str], rows: Sequence[Sequence[str]], *, names: Sequence[str] = ()
) -> str:
    """The text the model gets: headings, then one row of type tokens per sample row."""
    headings = [shown_heading(h, names=names) for h in header]
    return f"HEADINGS:\n{json.dumps(headings)}\nROWS:\n" + "\n".join(
        json.dumps([cell_token(c, names=names) for c in row]) for row in rows
    )


# --- from the model's answer to a layout ----------------------------------------------------


def _best_date_format(values: Sequence[str]) -> str | None:
    """The allowed format that reads the most of these values (UK day-first wins a tie)."""
    best, best_count = None, 0
    for fmt in ALLOWED_DATE_FORMATS:
        count = 0
        for value in values:
            try:
                datetime.strptime(value, fmt)
            except ValueError:
                continue
            count += 1
        if count > best_count:
            best, best_count = fmt, count
    return best


_KIND_WORDS = {"current": "a current account", "savings": "a savings account"}


def _priced(rows: Sequence[Sequence[str]], amount_at: int) -> list[Sequence[str]]:
    return [row for row in rows if len(row) > amount_at and row[amount_at].strip()]


def _direction_column(
    header: Sequence[str], rows: Sequence[Sequence[str]], amount_at: int, prefer: int | None
) -> int | None:
    """The column that says DR or CR (Debit or Credit) on every row with an amount, and both
    ways round somewhere, worked out on this device. A column with one value on every row
    (a card type, a cleared flag) says nothing about direction. The model's type column is
    tried first."""
    priced = _priced(rows, amount_at)
    order = [*([prefer] if prefer is not None else []), *range(len(header))]
    for i in order:
        if i == amount_at or not header[i].strip() or not priced:
            continue
        ways = {direction_of(row[i]) if len(row) > i else None for row in priced}
        if ways == {-1, 1}:
            return i
    return None


def marker_contradiction(doc: Document, layout: CsvLayout, parsed: ParsedStatement) -> str | None:
    """Why a DR/CR column and the amounts' own printed signs can't both be right, or None.

    When the amounts print their own signs, those win and the column is not the sign source;
    a row whose marker says the other way means one of them is wrong."""
    header = header_cells(doc)
    if header is None or layout.amount is None or layout.direction is not None:
        return None
    records = data_records(doc)
    amount_at = header.index(layout.amount)
    at = _direction_column(header, [cells for _, cells in records], amount_at, None)
    if at is None:
        return None
    marker = {ref: direction_of(cells[at]) for ref, cells in records if len(cells) > at}
    wrong = sum(
        1
        for row in parsed.rows
        if (way := marker.get(row.ref)) is not None and row.amount_pence * way < 0
    )
    if not wrong:
        return None
    return (
        f"The DR/CR column disagrees with the amounts' own signs on {_plural(wrong)}, so money "
        "in can't be told from money out. Please check the signs."
    )


def missing_sign_source(
    parsed: ParsedStatement, layout: CsvLayout, account_kind: AccountKind | None
) -> str | None:
    """Why a bank account's single amount column can't say which way its amounts go, or None.

    Something must mark money out: a minus, brackets or DR on some amount, or a DR/CR column.
    Without either, every row would be stored as money in."""
    if (
        account_kind not in _KIND_WORDS
        or layout.amount is None
        or layout.direction is not None
        or layout.perspective == "card"
        or not parsed.rows
        or any(row.amount_pence < 0 for row in parsed.rows)
    ):
        return None
    return (
        "No amount in this file is marked as money out (a minus, brackets or DR) and it has no "
        "DR/CR column, so money in can't be told from money out for "
        f"{_KIND_WORDS[account_kind]}. Please check the signs."
    )


def mapping_to_layout(
    mapping: MappingOut,
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    account_kind: AccountKind | None = None,
    names: Sequence[str] = (),
) -> CsvLayout:
    """A CsvLayout from the model's answer, or a MappingError naming what's wrong.

    The model names columns as it saw them; they are mapped back to the file's own headings.
    A hidden heading can't be chosen. The date format comes from every value in the date
    column (`rows` is every data row). On a bank account, a single amount column read as a
    card's (purchases positive) contradicts the account type and is refused. A column of DR/CR
    markers beside a single amount column printed without signs is found here and gives each
    amount its direction; amounts that print their own signs keep them."""
    shown = [shown_heading(h, names=names) for h in header]
    index: dict[str, int] = {}
    for i, name in enumerate(shown):
        if name and name != HIDDEN:
            index.setdefault(norm(name), i)

    def position(name: str, what: str) -> int:
        i = index.get(norm(name))
        if i is None:
            raise MappingError(f"Column '{tidy(name, 60)}' ({what}) isn't in the header")
        return i

    def column(name: str | None, what: str) -> str | None:
        return None if name is None else header[position(name, what)]

    date_at = position(mapping.date_column, "date_column")
    descriptions = [
        c for c in (column(d, "description_columns") for d in mapping.description_columns[:3]) if c
    ]
    if not descriptions:
        raise MappingError("description_columns needs at least one column from the header")
    if mapping.amount_column is None and not (mapping.money_out_column and mapping.money_in_column):
        raise MappingError(
            "Give either amount_column, or both money_out_column and money_in_column"
        )
    roles = {
        "merchant_column": mapping.merchant_column,
        "amount_column": mapping.amount_column,
        "money_out_column": None if mapping.amount_column else mapping.money_out_column,
        "money_in_column": None if mapping.amount_column else mapping.money_in_column,
        "balance_column": mapping.balance_column,
        "category_column": mapping.category_column,
        "type_column": mapping.type_column,
    }
    columns = {what: column(name, what) for what, name in roles.items()}
    direction = None
    if mapping.amount_column is not None:  # a DR/CR column signs amounts printed without one
        amount_at = position(mapping.amount_column, "amount_column")
        type_at = position(mapping.type_column, "type_column") if mapping.type_column else None
        at = _direction_column(header, rows, amount_at, type_at)
        if at is not None and not any(
            has_printed_sign(row[amount_at]) for row in _priced(rows, amount_at)
        ):
            direction = header[at]
    dates = [" ".join(row[date_at].split()) for row in rows if len(row) > date_at]
    date_format = _best_date_format([d for d in dates if d])
    if date_format is None:
        raise MappingError(
            f"Column '{shown[date_at]}' (date_column) doesn't hold dates in a format Tuppence "
            "can read"
        )
    card = mapping.amounts_are == "purchases_positive" and mapping.amount_column is not None
    if card and account_kind in _KIND_WORDS:
        raise MappingError(
            f"amounts_are can't be purchases_positive for {_KIND_WORDS[account_kind]}: "
            "a bank account's single amount column shows money out as negative"
        )
    return CsvLayout(
        id=learned_id(header, account_kind),
        name="Your bank's export (learned)",
        kind=account_kind,
        source="learned",
        signature=[h for h in header if h.strip()],
        date=header[date_at],
        date_formats=[date_format],
        description=descriptions,
        merchant=columns["merchant_column"],
        amount=columns["amount_column"],
        money_out=columns["money_out_column"],
        money_in=columns["money_in_column"],
        perspective="card" if card else "household",
        balance=columns["balance_column"],
        category=columns["category_column"],
        type=columns["type_column"] or direction,
        direction=direction,
    )


# --- feedback: counts and column names only -------------------------------------------------

_CHECK_KINDS = (
    ("larger than any real transaction", "Amounts are larger than any real transaction"),
    ("running balance mismatch", "Running balances don't add up"),
    ("balance mismatch", "Opening balance plus the amounts doesn't give the closing balance"),
    ("sign mismatch", "Money in and money out are the wrong way round"),
    ("sign_from", "Money in and money out are the wrong way round"),
    ("amount_text", "Amounts don't match their lines"),
    ("missing refs", "Some rows weren't read"),
    ("duplicate ref", "Some rows were read twice"),
    ("outside", "Dates fall outside the statement period"),
    ("period", "The statement period doesn't make sense"),
)


def _plural(n: int) -> str:
    return f"{n} row{'s' if n != 1 else ''}"


def summarise_checks(errors: Sequence[str]) -> list[str]:
    """Check failures as fixed descriptions with a count each. No text from a failure (which
    may quote a figure, a reference or a row) is kept."""
    counts: dict[str, int] = {}
    for error in errors:
        label = next((text for key, text in _CHECK_KINDS if key in error), "Other check failures")
        counts[label] = counts.get(label, 0) + 1
    return [f"{label} ({_plural(n)})" for label, n in counts.items()]


def _describe(
    problems: Sequence[str], layout: CsvLayout, header: Sequence[str], names: Sequence[str]
) -> list[str]:
    """Rows the importer couldn't read, counted by column. Problems quote cells, so only
    their kind is used."""
    shown = {h: shown_heading(h, names=names) for h in header}

    def name(column: str | None) -> str:
        return shown.get(column or "", "") or "?"

    dates = sum(1 for p in problems if "can't read the date" in p)
    amounts = sum(1 for p in problems if "can't read the amount" in p)
    both = sum(1 for p in problems if "both money out and money in" in p)
    out: list[str] = []
    if dates:
        out.append(
            f"Dates in column '{name(layout.date)}' couldn't be read as "
            f"{_format_name(layout.date_formats[0])} on {_plural(dates)}"
        )
    if amounts:
        where = (
            f"column '{name(layout.amount)}'"
            if layout.amount
            else f"columns '{name(layout.money_out)}' and '{name(layout.money_in)}'"
        )
        out.append(f"Amounts in {where} couldn't be read on {_plural(amounts)}")
    if both:
        out.append(
            f"Columns '{name(layout.money_out)}' and '{name(layout.money_in)}' are both filled "
            f"in on {_plural(both)}"
        )
    rest = len(problems) - dates - amounts - both
    if rest:
        out.append(f"{_plural(rest)} couldn't be read")
    return out


def propose_layout(
    doc: Document,
    *,
    llm: StructuredLLM,
    run: Any,
    account_kind: AccountKind | None = None,
    names: Sequence[str] = (),
    max_attempts: int = 3,
    prompt: str | None = None,
) -> MappingOutcome:
    """`names` are the household's own names: a heading or cell that is one is hidden."""
    header = header_cells(doc)
    if header is None:
        return MappingOutcome(
            layout=None,
            result=None,
            errors=[
                "This file has no row of column headings, so its layout can't be "
                "worked out automatically."
            ],
        )
    problem = header_problem(header, names=names)
    if problem is not None:
        return MappingOutcome(layout=None, result=None, errors=[problem])
    rows = [cells for _, cells in data_records(doc)]
    base = sketch(header, rows[:SAMPLE_ROWS], names=names)
    system = Message(role="system", content=prompt or load_prompt("csv_mapping"))
    errors: list[str] = []
    previous: str | None = None
    last: ImportResult | None = None
    for attempt in range(1, max_attempts + 1):
        user = (
            base
            if attempt == 1
            else (
                f"{base}\n\nYour previous answer didn't work:\n"
                + "\n".join(tidy(e) for e in errors[:20])
                + (f"\n{previous}" if previous else "")
            )
        )
        try:
            mapping = llm.structured(
                "read",
                [system, Message(role="user", content=user)],
                MappingOut,
                max_tokens=800,
                run=run,
            )
        except LLMBadResponse as exc:
            errors, previous = [tidy(safe_error_text(exc))], None
            continue
        previous = tidy(mapping.model_dump_json(), 2000)
        try:
            layout = mapping_to_layout(
                mapping, header, rows, account_kind=account_kind, names=names
            )
            last = parse_with_layout(doc, layout)
        except MappingError as exc:
            errors = [tidy(safe_error_text(exc))]
            continue
        except LayoutMismatch:
            errors = ["One of the columns in that answer isn't in the file"]
            continue
        errors = _describe(last.problems, layout, header, names) + summarise_checks(
            check_document(doc, last.parsed)
        )
        for doubt in (
            missing_sign_source(last.parsed, layout, account_kind),
            marker_contradiction(doc, layout, last.parsed),
        ):
            if doubt:
                errors.append(doubt)
        if not errors:
            return MappingOutcome(layout=layout, result=last, errors=[], attempts=attempt)
    return MappingOutcome(layout=None, result=last, errors=errors, attempts=max_attempts)
