"""Turn a statement into numbered lines and chunks (ported from v3, the predecessor app).

This module doesn't decide columns, signs or merchants. It decodes text, numbers
lines (`L12` for files, `P2L4` for pages), finds the CSV header row, separates the
preamble (address and account details, never sent to an AI model) from the data,
and groups lines into chunks a model can read.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Sequence

from pydantic import BaseModel

from tuppence.ingest.models import Document, FileKind, Line
from tuppence.ingest.textnum import decode_text, parse_date, parse_money

_DELIMITERS = (",", ";", "\t", "|")
_HEADING_WORDS = (
    "date",
    "description",
    "details",
    "amount",
    "balance",
    "paid out",
    "paid in",
    "money out",
    "money in",
    "debit",
    "credit",
    "type",
    "reference",
    "transaction",
    "payee",
    "memo",
    "withdrawals",
    "deposits",
    "narrative",
    "value",
    "category",
    "counter party",
)
_DATE_TOKEN = re.compile(
    r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?(?:\s+\d{2,4})?\b",
    re.IGNORECASE,
)
_MONEY_TOKEN = re.compile(r"(?<![\w.])[-−]?[£$]?\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)")


class Chunk(BaseModel):
    lines: list[Line]  # context lines first, then data lines, in file order
    context_refs: list[str]  # headings repeated for context: never transactions
    data_refs: list[str]  # each must come back exactly once as a row or a skip


def sniff_delimiter(text: str) -> str:
    sample = [ln for ln in text.split("\n")[:20] if ln.strip()][:8]
    if not sample:
        return ","
    best, best_score = ",", 0
    for delim in _DELIMITERS:
        counts = [ln.count(delim) for ln in sample]
        score = min(counts[-3:] or [0])  # data rows agree; a preamble may not
        if score > best_score:
            best, best_score = delim, score
    return best


def csv_records(text: str) -> list[tuple[int, str, list[str]]]:
    """(first physical line number, raw text, cells) per CSV record.

    A quoted field may contain a line break; the record keeps the number of its
    first physical line and its raw text has the breaks replaced by spaces.
    """
    delimiter = sniff_delimiter(text)
    physical = text.split("\n")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    out: list[tuple[int, str, list[str]]] = []
    start = 1
    for cells in reader:
        end = reader.line_num
        raw = " ".join(physical[start - 1 : end])
        out.append((start, raw, [c.strip() for c in cells]))
        start = end + 1
    return out


def is_money_cell(cell: str) -> bool:
    return any(ch.isdigit() for ch in cell) and parse_money(cell) is not None


def is_date_cell(cell: str) -> bool:
    return parse_date(cell) is not None


def find_header(
    rows: Sequence[Sequence[str]],
    *,
    known: Callable[[Sequence[str]], bool] | None = None,
    scan: int = 15,
) -> int | None:
    """Index of the column-heading row, or None for a file without one."""
    for i, cells in enumerate(rows[:scan]):
        filled = [c for c in cells if c.strip()]
        if len(filled) < 2:
            continue
        if known is not None and known(cells):
            return i
        if any(is_money_cell(c) or is_date_cell(c) for c in filled):
            continue
        following = [r for r in rows[i + 1 : i + 4] if any(c.strip() for c in r)]
        if (
            following
            and len(following[0]) == len(cells)
            and any(is_date_cell(c) for c in following[0])
        ):
            return i
    return None


def table_document(
    records: Sequence[tuple[int, str, list[str]]],
    *,
    sha256: str,
    kind: FileKind,
    known: Callable[[Sequence[str]], bool] | None = None,
) -> Document:
    kept = [(n, raw, cells) for n, raw, cells in records if any(c.strip() for c in cells)]
    lines = [Line(ref=f"L{n}", text=raw) for n, raw, _ in kept]
    table = [cells for _, _, cells in kept]
    header = find_header(table, known=known)
    refs = [line.ref for line in lines]
    if header is None:
        return Document(kind=kind, sha256=sha256, lines=lines, table=table, data_refs=refs)
    return Document(
        kind=kind,
        sha256=sha256,
        lines=lines,
        table=table,
        preamble_refs=refs[:header],
        header_refs=[refs[header]],
        data_refs=refs[header + 1 :],
    )


def csv_document(
    data: bytes, *, sha256: str, known: Callable[[Sequence[str]], bool] | None = None
) -> Document:
    return table_document(csv_records(decode_text(data)), sha256=sha256, kind="csv", known=known)


def is_anchor(text: str) -> bool:
    """The first line of the transaction area: a dated amount, or a row of column headings."""
    if _DATE_TOKEN.search(text) and _MONEY_TOKEN.search(text):
        return True
    lowered = text.casefold()
    return sum(1 for word in _HEADING_WORDS if re.search(rf"\b{re.escape(word)}\b", lowered)) >= 2


def split_preamble(lines: Sequence[Line]) -> tuple[list[str], list[str]]:
    """(preamble refs, data refs). With no anchor at all, everything is data."""
    for i, line in enumerate(lines):
        if is_anchor(line.text):
            return [ln.ref for ln in lines[:i]], [ln.ref for ln in lines[i:]]
    return [], [ln.ref for ln in lines]


def text_document(text: str, *, sha256: str) -> Document:
    lines = [
        Line(ref=f"L{n}", text=t.rstrip())
        for n, t in enumerate(text.split("\n"), start=1)
        if t.strip()
    ]
    preamble, data = split_preamble(lines)
    return Document(kind="text", sha256=sha256, lines=lines, preamble_refs=preamble, data_refs=data)


def pages_document(
    pages: Sequence[Sequence[str]], *, sha256: str, kind: FileKind, preamble: bool = True
) -> Document:
    lines: list[Line] = []
    for p, rows in enumerate(pages, start=1):
        n = 0
        for row in rows:
            if row.strip():
                n += 1
                lines.append(Line(ref=f"P{p}L{n}", text=row.rstrip()))
    pre, data = split_preamble(lines) if preamble else ([], [ln.ref for ln in lines])
    return Document(
        kind=kind, sha256=sha256, lines=lines, preamble_refs=pre, data_refs=data, pages=len(pages)
    )


def render(lines: Sequence[Line]) -> str:
    return "\n".join(f"{line.ref}: {line.text}" for line in lines)


def _heading_like(text: str) -> bool:
    return not _MONEY_TOKEN.search(text) and is_anchor(text)


def plan_chunks(doc: Document, *, rows_per_chunk: int) -> list[Chunk]:
    """Data lines in slices of `rows_per_chunk`, each with the headings it needs."""
    by_ref = doc.by_ref()
    order = {line.ref: i for i, line in enumerate(doc.lines)}
    data = [by_ref[r] for r in doc.data_refs]
    chunks: list[Chunk] = []
    for start in range(0, len(data), max(1, rows_per_chunk)):
        part = data[start : start + rows_per_chunk]
        context: list[Line] = [by_ref[r] for r in doc.header_refs]
        if not context and start > 0:
            # Page text: the last column-heading line, and the line just before the
            # chunk so the reader can see the previous running balance.
            first = order[part[0].ref]
            earlier = [
                ln for ln in doc.lines[:first] if ln.ref in doc.data_refs and _heading_like(ln.text)
            ]
            context = list({ln.ref: ln for ln in [*earlier[-1:], data[start - 1]]}.values())
        merged = {ln.ref: ln for ln in [*context, *part]}
        chunk_lines = sorted(merged.values(), key=lambda ln: order[ln.ref])
        chunks.append(
            Chunk(
                lines=chunk_lines,
                context_refs=[c.ref for c in context],
                data_refs=[ln.ref for ln in part],
            )
        )
    return chunks
