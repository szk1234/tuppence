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

from tuppence.core.errors import UserFacing
from tuppence.ingest.models import Document, FileKind, Line
from tuppence.ingest.textnum import decode_text, parse_date, parse_money

# One field may be up to 1 MB (the csv default of 128 KB crashes on a long note). This
# is process-wide and set once at import, so it is not racy.
csv.field_size_limit(1 << 20)


class UnreadableFile(UserFacing, ValueError):
    """A statement file we can't turn into lines."""


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
_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_NUMERIC_DATE = re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
_NAMED_DATE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH}\b\.?", re.IGNORECASE)
_MONEY_TOKEN = re.compile(r"(?<![\w.])[-−]?[£$]?\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)")
_STREET = (
    r"road|rd|street|st|lane|ln|avenue|ave|close|drive|way|gardens|court|place|terrace"
    r"|crescent|square|hill|grove|mews|walk"
)
# Lines that identify the holder or the account. Never data, wherever they appear.
_SENSITIVE = re.compile(
    r"\b(?:account|acct?|a/c)\.?\s*(?:no\.?|num(?:ber)?|name|holders?|type)\b"
    r"|\b(?:customer|membership|roll)\s*(?:no\.?|num(?:ber)?|id)\b"
    r"|(?<![\w/])(?:account|acct?|a/c|s/c|s\.c\.)\.?\s*:?\s*#?\s*\d[\d -]{4,}\d"
    r"|\bsort\s*code\b|\b\d{2}-\d{2}-\d{2}\b"
    r"|\bcard\s+(?:ending|number|no\.?)\b|\bending\s+(?:in\s+)?\d{4}\b"
    r"|\*{2,}[\s-]*\d{2,4}|(?<![a-z])x{2,}[\s-]*\d{4}\b|\b\d{4}[\s-]*\*{2,}"
    r"|\b(?:\d[ -]?){12,18}\d\b"
    r"|\biban\b|\bbic\b|\b[A-Z]{2}\d{2}\s?[A-Z0-9]{4}(?:\s?\d{4}){2,}(?:\s?[A-Z0-9]{1,4})?\b"
    r"|\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b"
    rf"|(?<![\d/.-])\d{{1,3}}[a-z]?,?\s+(?:[A-Za-z']+\s+){{1,2}}(?:{_STREET})\b"
    r"|^\s*(?:mr|mrs|ms|miss|mx|dr|prof)\.?\s+[A-Za-z]"
    r"|^\s*(?:statement\s+for|prepared\s+for|holder)\b",
    re.IGNORECASE,
)
# "Opening balance 1,000.00", "Money in 1,200.00 Money out 800.00": figures, not rows.
_SUMMARY = re.compile(
    r"\b(?:opening|closing|start|end)\s+balance\b|\bmoney\s+(?:in|out)\b"
    r"|\bpaid\s+(?:in|out)\b|\btotal\b|\bpayments?\s+(?:in|out)\b|\bsummary\b",
    re.IGNORECASE,
)
_PAGE_NO = re.compile(r"\bpage\s+\d+(?:\s+of\s+\d+)?\b", re.IGNORECASE)
_FURNITURE = re.compile(
    r"\bpage\s+\d+\b|\bcontinued\b|\bstatement\s+(?:period|date)\b", re.IGNORECASE
)


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
    try:
        for cells in reader:
            end = reader.line_num
            raw = " ".join(physical[start - 1 : end])
            out.append((start, raw, [c.strip() for c in cells]))
            start = end + 1
    except csv.Error:
        raise UnreadableFile("This file has a value that's too long to read.") from None
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


def _has_date(text: str) -> bool:
    if any(parse_date(m.group(0)) is not None for m in _NUMERIC_DATE.finditer(text)):
        return True
    return any(1 <= int(m.group(1)) <= 31 for m in _NAMED_DATE.finditer(text))


def is_sensitive(text: str) -> bool:
    """Account numbers, sort codes, card endings, IBANs, addresses and holder names."""
    return _SENSITIVE.search(text) is not None


def is_summary(text: str) -> bool:
    """A balance or totals line: it has figures but is not a transaction."""
    return (
        _SUMMARY.search(text) is not None
        and _MONEY_TOKEN.search(text) is not None
        and not re.match(
            r"\s*(?:\d{1,2}[/.-]\d|\d{4}-|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3})", text
        )
    )


def is_heading(text: str) -> bool:
    lowered = text.casefold()
    return (
        sum(1 for word in _HEADING_WORDS if re.search(rf"\b{re.escape(word)}\b", lowered)) >= 2
        and not _MONEY_TOKEN.search(text)
        and ":" not in text  # "Payment type: Direct debit" is a label, not column headings
        and not is_sensitive(text)
    )


def is_anchor(text: str) -> bool:
    """The first line of the transaction area: a dated amount, or a row of column headings."""
    if is_sensitive(text) or is_summary(text):
        return False
    if _has_date(text) and _MONEY_TOKEN.search(text):
        return True
    return is_heading(text)


def _normal(text: str) -> str:
    return " ".join(_PAGE_NO.sub(" ", text).casefold().split())


_TITLE = re.compile(r"^(?:mr|mrs|ms|miss|mx|dr|prof)\.?\s+", re.IGNORECASE)
_NAME_LINE = re.compile(r"^(?:[A-Z][A-Za-z'’-]*)(?:\s+[A-Z][A-Za-z'’-]*){1,3}$")
_PAGE_REF = re.compile(r"P(\d+)L\d+")


def _name_key(text: str) -> str | None:
    """Normalised form of a line that looks like a person's name, else None."""
    stripped = _TITLE.sub("", text.strip())
    if not _NAME_LINE.fullmatch(stripped) or any(ch.isdigit() for ch in stripped):
        return None
    return " ".join(stripped.casefold().split())


def _page_edge_repeats(lines: Sequence[Line]) -> set[str]:
    """Normalised texts at the top (first 4) or bottom (last 3) of two or more pages."""
    by_page: dict[str, list[Line]] = {}
    for line in lines:
        match = _PAGE_REF.fullmatch(line.ref)
        if match:
            by_page.setdefault(match.group(1), []).append(line)
    seen: dict[str, set[str]] = {}
    for page, rows in by_page.items():
        for line in [*rows[:4], *rows[-3:]]:
            seen.setdefault(_normal(line.text), set()).add(page)
    return {text for text, pages in seen.items() if len(pages) >= 2}


def split_preamble(
    lines: Sequence[Line], *, names: Sequence[str] = ()
) -> tuple[list[str], list[str]]:
    """(withheld refs, data refs).

    Withheld lines never go to an AI reader: everything before the first anchor, and, after
    it, any account or address line, balance summary, or repeated page furniture. A
    repeat is furniture when it has a page marker, sits at the edge of 2+ pages, or has
    no figures or date and is a holder's name (`names`, or a name line in the preamble).
    """
    counts: dict[str, int] = {}
    for line in lines:
        counts[_normal(line.text)] = counts.get(_normal(line.text), 0) + 1
    first = next((i for i, ln in enumerate(lines) if is_anchor(ln.text)), None)
    edge = _page_edge_repeats(lines)
    known_names = {k for n in names if (k := _name_key(n))}
    for line in lines[: first if first is not None else 0]:
        if key := _name_key(line.text):
            known_names.add(key)
    withheld: list[str] = []
    data: list[str] = []
    for i, line in enumerate(lines):
        text = line.text
        key = _normal(text)
        plain = not _MONEY_TOKEN.search(text) and not _has_date(text)
        furniture = (
            counts[key] > 1
            and not _MONEY_TOKEN.search(text)
            and not is_heading(text)
            and (_FURNITURE.search(text) is not None or key in edge)
        )
        name_repeat = plain and _name_key(text) in known_names and _name_key(text) is not None
        if (
            (first is not None and i < first)
            or is_sensitive(text)
            or is_summary(text)
            or furniture
            or name_repeat
        ):
            withheld.append(line.ref)
        else:
            data.append(line.ref)
    return withheld, data


def text_document(text: str, *, sha256: str, names: Sequence[str] = ()) -> Document:
    lines = [
        Line(ref=f"L{n}", text=t.rstrip())
        for n, t in enumerate(text.split("\n"), start=1)
        if t.strip()
    ]
    preamble, data = split_preamble(lines, names=names)
    return Document(kind="text", sha256=sha256, lines=lines, preamble_refs=preamble, data_refs=data)


def pages_document(
    pages: Sequence[Sequence[str]],
    *,
    sha256: str,
    kind: FileKind,
    preamble: bool = True,
    names: Sequence[str] = (),
) -> Document:
    lines: list[Line] = []
    for p, rows in enumerate(pages, start=1):
        n = 0
        for row in rows:
            if row.strip():
                n += 1
                lines.append(Line(ref=f"P{p}L{n}", text=row.rstrip()))
    pre, data = split_preamble(lines, names=names) if preamble else ([], [ln.ref for ln in lines])
    return Document(
        kind=kind, sha256=sha256, lines=lines, preamble_refs=pre, data_refs=data, pages=len(pages)
    )


def render(lines: Sequence[Line]) -> str:
    return "\n".join(f"{line.ref}: {line.text}" for line in lines)


def plan_chunks(doc: Document, *, rows_per_chunk: int) -> list[Chunk]:
    """Data lines in slices of `rows_per_chunk`, each with the headings it needs."""
    by_ref = doc.by_ref()
    order = {line.ref: i for i, line in enumerate(doc.lines)}
    data = [by_ref[r] for r in doc.data_refs]
    step = max(1, rows_per_chunk)
    # last heading-like data line before each slice start, found in one pass
    last_heading: dict[int, Line | None] = {}
    current: Line | None = None
    for i, line in enumerate(data):
        if i % step == 0:
            last_heading[i] = current
        if not doc.header_refs and is_heading(line.text):
            current = line
    chunks: list[Chunk] = []
    for start in range(0, len(data), step):
        part = data[start : start + step]
        context: list[Line] = [by_ref[r] for r in doc.header_refs]
        if not context and start > 0:
            # Page text: the last column-heading line, and the line just before the
            # chunk so the reader can see the previous running balance.
            heading = last_heading[start]
            context = list(
                {ln.ref: ln for ln in [*([heading] if heading else []), data[start - 1]]}.values()
            )
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
