"""QIF (Quicken Interchange Format) bank and card files."""

from __future__ import annotations

import re
from collections.abc import Sequence

from tuppence.core.errors import UserFacing
from tuppence.ingest.models import Document, Line, ParsedRow, ParsedStatement
from tuppence.ingest.textnum import parse_date, parse_money, to_pence


class QifError(UserFacing, ValueError):
    pass


def _records(text: str) -> tuple[str, list[tuple[int, list[str]]]]:
    kind = ""
    records: list[tuple[int, list[str]]] = []
    current: list[str] = []
    start = 0
    for n, raw in enumerate(text.split("\n"), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("!"):
            if line.lower().startswith("!type:"):
                kind = line[6:].strip()
            continue
        if line == "^":
            if current:
                records.append((start, current))
            current = []
            continue
        if not current:
            start = n
        current.append(line)
    if current:
        records.append((start, current))
    if not kind:
        raise QifError("This doesn't look like a QIF file.")
    return kind, records


def _date_formats(raw_dates: Sequence[str]) -> tuple[str, ...]:
    """UK files are day-first; switch to month-first only when a date proves it."""
    pairs = [re.split(r"[/\-.']", d.replace(" ", "")) for d in raw_dates]
    month_first = any(len(p) >= 2 and p[1].isdigit() and int(p[1]) > 12 for p in pairs)
    return ("%m/%d/%Y", "%m/%d/%y") if month_first else ("%d/%m/%Y", "%d/%m/%y")


def _clean_date(raw: str) -> str:
    return raw.replace("'", "/").replace("-", "/").replace(".", "/").replace(" ", "")


def qif_document(text: str, *, sha256: str) -> Document:
    kind, records = _records(text)
    lines = [Line(ref="H1", text=f"!Type:{kind}")]
    lines += [Line(ref=f"L{start}", text=" | ".join(fields)) for start, fields in records]
    return Document(
        kind="qif",
        sha256=sha256,
        lines=lines,
        preamble_refs=["H1"],
        data_refs=[f"L{start}" for start, _ in records],
        meta={"qif_type": kind, "card": "1" if kind.lower() in {"ccard", "credit card"} else "0"},
    )


def parse_qif(text: str) -> ParsedStatement:
    _, records = _records(text)
    fields_list = [{f[0]: f[1:].strip() for f in reversed(fields)} for _, fields in records]
    formats = _date_formats([_clean_date(f.get("D", "")) for f in fields_list])
    parsed = ParsedStatement(importer="qif", perspective="household")
    for (start, _), fields in zip(records, fields_list, strict=True):
        day = parse_date(_clean_date(fields.get("D", "")), formats)
        amount_text = fields.get("T") or fields.get("U") or ""
        amount = parse_money(amount_text)
        if day is None or amount is None:
            continue
        payee, memo = fields.get("P", ""), fields.get("M", "")
        parsed.rows.append(
            ParsedRow(
                ref=f"L{start}",
                date=day,
                amount_pence=to_pence(amount),
                amount_text=amount_text,
                raw_description=" ".join(p for p in (payee, memo) if p) or "(no description)",
                merchant=payee or None,
                bank_category=fields.get("L") or None,
            )
        )
    if parsed.rows:
        parsed.period_start = min(r.date for r in parsed.rows)
        parsed.period_end = max(r.date for r in parsed.rows)
    return parsed
