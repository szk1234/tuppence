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
from dataclasses import dataclass, field

from pydantic import BaseModel

from tuppence.core.errors import UserFacing
from tuppence.ingest import sensitive
from tuppence.ingest.models import Document, FileKind, Line, MaskedLine
from tuppence.ingest.textnum import decode_text, parse_date, parse_money

# One field may be up to 1 MB (the csv default of 128 KB crashes on a long note). This
# is process-wide and set once at import, so it is not racy.
csv.field_size_limit(1 << 20)


class UnreadableFile(UserFacing, ValueError):
    """A statement file we can't turn into lines."""


# A statement line is never this long. A longer one isn't read (no pattern runs over it): it is
# kept cut short, left out of what is sent, and reported so the person can say what it is.
MAX_LINE_CHARS = 10_000
Deadline = Callable[[], object]  # raises once the extraction's time is up


def _no_deadline() -> None:
    return None


def _ticking(deadline: Deadline | None, every: int = 256) -> Callable[[int], None]:
    """A per-line hook that calls `deadline` every `every` lines."""
    check = deadline or _no_deadline

    def tick(i: int) -> None:
        if i % every == 0:
            check()

    return tick


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
# A figure with pence. It never starts inside a run of thousands groups ("1,000,000"): a start
# right after "digit," is allowed only when it isn't itself a 3-digit group followed by another
# comma, so each run is tried once and a long run of groups stays fast.
_MONEY_TOKEN = re.compile(
    r"(?<![\w.])(?:(?<!\d,)|(?!\d{3},))[-−]?[£$]?\d{1,3}(?:,\d{3})*\.\d{2}(?!\d|\.\d)"
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


def csv_records(text: str, *, deadline: Deadline | None = None) -> list[tuple[int, str, list[str]]]:
    """(first physical line number, raw text, cells) per CSV record.

    A quoted field may contain a line break; the record keeps the number of its
    first physical line and its raw text has the breaks replaced by spaces.
    """
    delimiter = sniff_delimiter(text)
    physical = text.split("\n")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    out: list[tuple[int, str, list[str]]] = []
    start = 1
    tick = _ticking(deadline)
    try:
        for cells in reader:
            tick(len(out))
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
    data: bytes,
    *,
    sha256: str,
    known: Callable[[Sequence[str]], bool] | None = None,
    deadline: Deadline | None = None,
) -> Document:
    records = csv_records(decode_text(data), deadline=deadline)
    return table_document(records, sha256=sha256, kind="csv", known=known)


def _has_date(text: str) -> bool:
    if any(parse_date(m.group(0)) is not None for m in _NUMERIC_DATE.finditer(text)):
        return True
    return any(1 <= int(m.group(1)) <= 31 for m in _NAMED_DATE.finditer(text))


def is_sensitive(text: str, *, names: Sequence[str] = ()) -> bool:
    """Account numbers, sort codes, card endings, IBANs, addresses and holder names (the
    shared classifier in `ingest.sensitive`; `names` are the household's own names)."""
    return sensitive.is_sensitive(text, names=names)


def is_summary(text: str) -> bool:
    """A balance or totals line: it has figures but is not a transaction."""
    return (
        _SUMMARY.search(text) is not None
        and _MONEY_TOKEN.search(text) is not None
        and not re.match(
            r"\s*(?:\d{1,2}[/.-]\d|\d{4}-|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3})", text
        )
    )


_SUMMARY_WORDS = re.compile(
    r"\b(?:opening|closing|start|end|balance|money|paid|payments?|in|out|totals?|summary|of|on"
    r"|at|as|for|the|this|period|month|statement|gbp)\b",
    re.IGNORECASE,
)


def _pure_summary(text: str) -> bool:
    """A totals line with nothing else on it ("Money in £1,650.00 Money out £438.78", "Total
    1,234.56"). "TOTAL ENERGIES 45.00" has a payee's name, so it may be a row."""
    rest = _SUMMARY_WORDS.sub(" ", _MONEY_TOKEN.sub(" ", _CURRENCY_FIGURE.sub(" ", text)))
    rest = _NAMED_DATE.sub(" ", _NUMERIC_DATE.sub(" ", rest))
    return re.search(r"[A-Za-z]", rest) is None


def is_heading(text: str) -> bool:
    lowered = text.casefold()
    return (
        sum(1 for word in _HEADING_WORDS if re.search(rf"\b{re.escape(word)}\b", lowered)) >= 2
        and not _MONEY_TOKEN.search(text)
        and ":" not in text  # "Payment type: Direct debit" is a label, not column headings
        and not is_sensitive(text)
    )


def is_row(text: str, *, names: Sequence[str] = ()) -> bool:
    """A line shaped like a transaction: a date and an amount, and nothing that makes it a
    header line (a balance, a limit, a payment due or a summary). Account details inside it
    are fine when they can be masked (`sensitive.mask_line`)."""
    if not _has_date(text) or _MONEY_TOKEN.search(text) is None or is_summary(text):
        return False
    return not is_sensitive(text, names=names) or sensitive.mask_line(text, names=names) is not None


def is_anchor(text: str) -> bool:
    """A line that may start the transaction area: a row, or a row of column headings."""
    return is_row(text) or is_heading(text)


RUN_GAP = 2  # rows of a run are at most this far apart (one description line between)


def _table_start(rows: Sequence[bool], headings: Sequence[bool], lo: int, hi: int) -> int | None:
    """Where the transactions start among lines `lo`..`hi`: the column-heading row, unless a
    run of rows (two rows at most `RUN_GAP` apart) comes before it; else the first row.

    A single dated line above the address (a balance on a date, a payment due) is not the
    table: with a heading row or a run of rows below it, it stays in the header."""
    heading = next((i for i in range(lo, hi) if headings[i]), None)
    shaped = [i for i in range(lo, hi) if rows[i]]
    run = next((a for a, b in zip(shaped, shaped[1:], strict=False) if b - a <= RUN_GAP), None)
    if run is not None and (heading is None or run < heading):
        return run
    if heading is not None:
        return heading
    return shaped[0] if shaped else None


# A line that is part of an address without being one on its own: a house or building name, or
# a street word. "Exampletown" alone is not; in a block with a postcode or two of these, it is.
_ADDRESS_WORD = re.compile(
    r"\b(?:house|cottage|farm|lodge|mill|manor|court|mansions|building|barn|hall|villas?|"
    r"road|street|lane|avenue|close|drive|gardens|place|terrace|crescent|square|grove|mews|"
    r"walk|way|row|green|park|rise|view|hill)\b",
    re.IGNORECASE,
)


def _plain_short(text: str) -> bool:
    """A short line with no amount, no date and no column headings: an address line may be."""
    return (
        len(text.split()) <= 8
        and not _MONEY_TOKEN.search(text)
        and not _CURRENCY_FIGURE.search(text)
        and not _has_date(text)
        and not is_heading(text)
    )


def _address_blocks(texts: Sequence[str]) -> set[int]:
    """Lines of every address block: two or more short plain lines in a row, one of them a
    postcode or two of them address parts. Every line of the block is withheld, the town and
    a house name too (they aren't sensitive on their own)."""
    postcode = sensitive.VALUES["postcode"]
    address = sensitive.VALUES["address"]
    found: set[int] = set()
    i = 0
    while i < len(texts):
        j = i
        while j < len(texts) and _plain_short(texts[j]):
            j += 1
        block = range(i, j)
        if len(block) >= 2 and (
            any(postcode.search(texts[k]) for k in block)
            or sum(1 for k in block if address.search(texts[k]) or _ADDRESS_WORD.search(texts[k]))
            >= 2
        ):
            found.update(block)
        i = max(j, i + 1)
    return found


def _normal(text: str) -> str:
    return " ".join(_PAGE_NO.sub(" ", text).casefold().split())


_PAGE_REF = re.compile(r"P(\d+)L\d+")
_name_key = sensitive.name_key


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


_DAY_WORDS = re.compile(
    r"\b(?:today|yesterday|tomorrow|pending|earlier|(?:mon|tues|wednes|thurs|fri|satur|sun)day"
    r"|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b",
    re.IGNORECASE,
)
_SECTION = re.compile(
    r"^\s*(?:(?:recent|latest|all|account)\s+)?(?:transactions|activity|payments)\s*$",
    re.IGNORECASE,
)


def _owns_amount(text: str) -> bool:
    """Words that may be a row's description with its amount on the line above or below: no
    amount, and not a date or day heading, a section title or a balance label."""
    if (
        has_amount(text)
        or _SECTION.match(text)
        or sensitive.is_balance_label(text)
        or sensitive.is_balance_line(text)
    ):
        return False
    rest = _DAY_WORDS.sub(" ", _NAMED_DATE.sub(" ", _NUMERIC_DATE.sub(" ", text)))
    return re.search(r"[A-Za-z]", rest) is not None


def _balance_blocks(texts: Sequence[str]) -> list[range]:
    """Runs of lines that are balance labels or figures on their own, with at least one of
    each: "Available balance" over "£1,184.56", "£1,184.56" over "Balance". Every line of a
    run is held back, since any of its figures may be the balance."""
    label = [sensitive.is_balance_label(t) for t in texts]
    figure = [sensitive.is_figure_line(t) for t in texts]
    blocks: list[range] = []
    i = 0
    while i < len(texts):
        j = i
        while j < len(texts) and (label[j] or figure[j]):
            j += 1
        run = range(i, j)
        if any(label[k] for k in run) and any(figure[k] for k in run):
            blocks.append(run)
        i = max(j, i + 1)
    return blocks


def _unsure(texts: Sequence[str], block: range, header_end: int) -> set[int]:
    """The figures of a held-back block that may be a row's amount, so they are reported:
    all of them when there are more figures than labels (which is the balance can't be told),
    else a figure at the edge of the block beside words that may be its row's description
    (above it only once past the header, where a title like "Current Account" sits)."""
    figures = [k for k in block if sensitive.is_figure_line(texts[k])]
    if 2 * len(figures) > len(block):
        return set(figures)
    unsure: set[int] = set()
    top, bottom = block[0], block[-1]
    if top in figures and top - 1 >= header_end and _owns_amount(texts[top - 1]):
        unsure.add(top)
    if bottom in figures and bottom + 1 < len(texts) and _owns_amount(texts[bottom + 1]):
        unsure.add(bottom)
    return unsure


_CARRIED = re.compile(r"\b(?:brought|carried)\s+forward\b", re.IGNORECASE)


def _later_page_headers(
    lines: Sequence[Line],
    first: int | None,
    rows: Sequence[bool],
    headings: Sequence[bool],
) -> set[int]:
    """Each later page's header: the lines of every page after the table's first page that
    come before that page's own table start (all of a page that has none). A page repeats the
    name, address and account details above its rows, and they never go to an AI reader.
    A "balance brought forward" line stays data, as it is everywhere past the first anchor."""
    if first is None or not (start := _PAGE_REF.fullmatch(lines[first].ref)):
        return set()
    pages: dict[str, list[int]] = {}
    for i in range(first + 1, len(lines)):
        match = _PAGE_REF.fullmatch(lines[i].ref)
        if match is not None and match.group(1) != start.group(1):
            pages.setdefault(match.group(1), []).append(i)
    held: set[int] = set()
    for members in pages.values():
        lo, hi = members[0], members[-1] + 1
        table = _table_start(rows, headings, lo, hi)
        end = hi if table is None else table
        held.update(i for i in members if i < end and not _CARRIED.search(lines[i].text))
    return held


def split_preamble(
    lines: Sequence[Line], *, names: Sequence[str] = ()
) -> tuple[list[str], list[str]]:
    """(withheld refs, data refs).

    Withheld lines never go to an AI reader: everything before the first anchor, each later
    page's header (before that page's first anchor), and, after them, any account or address
    line, balance or balance summary, or repeated page furniture. A repeat is furniture when it
    has a page marker, sits at the edge of 2+ pages, or has no figures or date and is a
    holder's name (`names`, or a name line in the preamble).
    """
    split = _split_preamble(lines, names=names)
    return split.withheld, split.data


@dataclass
class _Split:
    withheld: list[str] = field(default_factory=list)
    data: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)  # withheld lines that may be rows: reported
    masked: dict[str, MaskedLine] = field(default_factory=dict)  # data sent with details masked

    def with_too_long(self, too_long: Sequence[str]) -> _Split:
        self.withheld += too_long
        self.held += too_long
        return self


class _Masks:
    """`sensitive.mask_line` once per line."""

    def __init__(self, texts: Sequence[str], names: Sequence[str]) -> None:
        self.texts, self.names = texts, names
        self.done: dict[int, MaskedLine | None] = {}

    def __call__(self, i: int) -> MaskedLine | None:
        if i not in self.done:
            self.done[i] = sensitive.mask_line(self.texts[i], names=self.names)
        return self.done[i]


def _split_preamble(
    lines: Sequence[Line],
    *,
    names: Sequence[str] = (),
    too_long: frozenset[str] = frozenset(),
    deadline: Deadline | None = None,
) -> _Split:
    """`split_preamble`, plus the withheld lines that may be rows, which are reported rather
    than lost: a dated amount above the table start, a figure in a balance block that may be a
    row's amount (`_unsure`), a line with an amount in a later page's header, and a row whose
    account details can't be masked. Rows with account details are sent with them masked
    (`sensitive.mask_line`). Lines in `too_long` are withheld and reported without being read.

    Past the table start, the only lines with an amount that are withheld and not reported are
    balances (read on this device) and totals."""
    if too_long:  # read as if empty; added back, withheld and reported, at the end
        kept = [line for line in lines if line.ref not in too_long]
        return _split_preamble(kept, names=names, deadline=deadline).with_too_long(
            [line.ref for line in lines if line.ref in too_long]
        )
    tick = _ticking(deadline)
    counts: dict[str, int] = {}
    for i, line in enumerate(lines):
        tick(i)
        counts[_normal(line.text)] = counts.get(_normal(line.text), 0) + 1
    texts = [line.text for line in lines]
    sensitive_at = [is_sensitive(t, names=names) for t in texts]
    masks = _Masks(texts, names)
    rows = [
        _has_date(t)
        and _MONEY_TOKEN.search(t) is not None
        and not is_summary(t)
        and (not sensitive_at[i] or masks(i) is not None)
        for i, t in enumerate(texts)
    ]
    headings = [is_heading(t) for t in texts]
    first = _table_start(rows, headings, 0, len(lines))
    edge = _page_edge_repeats(lines)
    blocks = _balance_blocks(texts)
    balances = {k for block in blocks for k in block}
    addresses = _address_blocks(texts)
    known_names = {k for n in names if (k := _name_key(n))}
    for line in lines[: first if first is not None else 0]:
        if key := _name_key(line.text):
            known_names.add(key)
    page_headers = _later_page_headers(lines, first, rows, headings)
    out = _Split()
    unsure: set[int] = set()
    for i, line in enumerate(lines):
        tick(i)
        text = line.text
        key = _normal(text)
        money = _MONEY_TOKEN.search(text) is not None
        plain = not money and not _has_date(text)
        furniture = (
            counts[key] > 1
            and not money
            and not is_heading(text)
            and (_FURNITURE.search(text) is not None or key in edge)
        )
        name_repeat = plain and _name_key(text) in known_names and _name_key(text) is not None
        if first is not None and i < first:
            out.withheld.append(line.ref)
            if rows[i]:  # a dated amount above the table start: it may be a row, so report it
                unsure.add(i)
        elif i in balances or (sensitive_at[i] and sensitive.is_masked_balance(text, names=names)):
            out.withheld.append(line.ref)  # a balance: read on this device, never a row
        elif i in page_headers:
            out.withheld.append(line.ref)
            if money and (not sensitive_at[i] or masks(i) is not None):
                unsure.add(i)  # may be a row above the page's first anchor
        elif (
            i in addresses or furniture or name_repeat or (is_summary(text) and _pure_summary(text))
        ):
            out.withheld.append(line.ref)
        elif sensitive_at[i]:
            masked = masks(i) if money else None
            if masked is None:  # no amount, so no row is lost; or details that won't mask
                out.withheld.append(line.ref)
                if money:
                    unsure.add(i)
            else:
                out.data.append(line.ref)
                if masked.hidden:
                    out.masked[line.ref] = masked
        else:
            out.data.append(line.ref)
    for block in blocks:
        if first is not None and block[0] > first:  # the summary box above the table never is
            unsure |= _unsure(texts, block, first)
    out.held = [lines[k].ref for k in sorted(unsure)]
    return out


_CURRENCY_FIGURE = re.compile(r"[£$€]\s?\d")


def has_amount(text: str) -> bool:
    """A money figure on the line: 12.30 with pence, or a currency sign before digits."""
    return _MONEY_TOKEN.search(text) is not None or _CURRENCY_FIGURE.search(text) is not None


def split_screenshot(
    lines: Sequence[Line], *, names: Sequence[str] = ()
) -> tuple[list[str], list[str]]:
    """(withheld refs, data refs) for a screenshot from a banking app.

    Apps list pending and "Today" rows without a date, so only the lines above the first line
    with a date or an amount (a balance doesn't count) are the header. After it, a line is
    withheld when it shows account details or a balance, or is only a name."""
    split = _split_screenshot(lines, names=names)
    return split.withheld, split.data


def _split_screenshot(
    lines: Sequence[Line],
    *,
    names: Sequence[str] = (),
    too_long: frozenset[str] = frozenset(),
    deadline: Deadline | None = None,
) -> _Split:
    """`split_screenshot`, plus the withheld lines with an amount that may be a transaction:
    every one but a plain balance. The parse step reports them rather than lose them. A row with
    account details is sent with them masked. Lines in `too_long` are withheld and reported
    without being read."""
    if too_long:
        kept = [line for line in lines if line.ref not in too_long]
        return _split_screenshot(kept, names=names, deadline=deadline).with_too_long(
            [line.ref for line in lines if line.ref in too_long]
        )
    tick = _ticking(deadline)
    texts = [line.text for line in lines]
    blocks = _balance_blocks(texts)
    balances = {k for block in blocks for k in block}
    balances |= {i for i, text in enumerate(texts) if sensitive.is_balance_line(text)}
    first = next(
        (
            i
            for i, text in enumerate(texts)
            if (_has_date(text) or has_amount(text)) and i not in balances
        ),
        len(lines),
    )
    unsure: set[int] = set()
    for block in blocks:
        unsure |= _unsure(texts, block, first)
    out = _Split()
    for i, line in enumerate(lines):
        tick(i)
        text = line.text
        masked: MaskedLine | None = None
        if (
            i >= first
            and i not in balances
            and _name_key(text) is None
            and is_sensitive(text, names=names)
            and has_amount(text)
        ):
            if sensitive.is_masked_balance(text, names=names):
                balances.add(i)  # a balance with a detail on it: withheld, not reported
            else:
                masked = sensitive.mask_line(text, names=names)
        if masked is not None:  # a row with account details: sent with them masked
            out.data.append(line.ref)
            if masked.hidden:
                out.masked[line.ref] = masked
        elif (
            i < first
            or i in balances
            or is_sensitive(text, names=names)
            or _name_key(text) is not None
        ):
            out.withheld.append(line.ref)
            if has_amount(text) and (i not in balances or i in unsure):
                out.held.append(line.ref)
        else:
            out.data.append(line.ref)
    return out


def _capped(ref: str, text: str, too_long: list[str]) -> Line:
    """A line, cut short (and listed in `too_long`) when it is over the cap."""
    if len(text) > MAX_LINE_CHARS:
        too_long.append(ref)
        return Line(ref=ref, text=text[:MAX_LINE_CHARS] + "…")
    return Line(ref=ref, text=text)


def _in_file_order(refs: Sequence[str], lines: Sequence[Line]) -> list[str]:
    wanted = set(refs)
    return [line.ref for line in lines if line.ref in wanted]


def text_document(
    text: str, *, sha256: str, names: Sequence[str] = (), deadline: Deadline | None = None
) -> Document:
    tick = _ticking(deadline)
    too_long: list[str] = []
    lines: list[Line] = []
    for n, raw in enumerate(text.split("\n"), start=1):
        tick(n)
        if raw.strip():
            lines.append(_capped(f"L{n}", raw.rstrip(), too_long))
    split = _split_preamble(lines, names=names, too_long=frozenset(too_long), deadline=deadline)
    return Document(
        kind="text",
        sha256=sha256,
        lines=lines,
        preamble_refs=_in_file_order(split.withheld, lines),
        data_refs=split.data,
        held_amount_refs=_in_file_order(split.held, lines),
        too_long_refs=too_long,
        masked=split.masked,
    )


def pages_document(
    pages: Sequence[Sequence[str]],
    *,
    sha256: str,
    kind: FileKind,
    preamble: bool = True,
    names: Sequence[str] = (),
    deadline: Deadline | None = None,
) -> Document:
    tick = _ticking(deadline)
    too_long: list[str] = []
    lines: list[Line] = []
    for p, rows in enumerate(pages, start=1):
        n = 0
        for row in rows:
            tick(len(lines) + 1)
            if row.strip():
                n += 1
                lines.append(_capped(f"P{p}L{n}", row.rstrip(), too_long))
    long = frozenset(too_long)
    if not preamble:
        split = _Split(data=[ln.ref for ln in lines if ln.ref not in long]).with_too_long(too_long)
    elif kind == "image":
        split = _split_screenshot(lines, names=names, too_long=long, deadline=deadline)
    else:
        split = _split_preamble(lines, names=names, too_long=long, deadline=deadline)
    return Document(
        kind=kind,
        sha256=sha256,
        lines=lines,
        preamble_refs=_in_file_order(split.withheld, lines),
        data_refs=split.data,
        held_amount_refs=_in_file_order(split.held, lines),
        too_long_refs=too_long,
        masked=split.masked,
        pages=len(pages),
    )


def render(lines: Sequence[Line]) -> str:
    return "\n".join(f"{line.ref}: {line.text}" for line in lines)


def sent_lines(doc: Document) -> dict[str, Line]:
    """Every line by ref as the AI reader may be sent it: a line with account details in it has
    them masked."""
    lines = doc.by_ref()
    for ref, masked in doc.masked.items():
        lines[ref] = Line(ref=ref, text=masked.text)
    return lines


def plan_chunks(doc: Document, *, rows_per_chunk: int) -> list[Chunk]:
    """Data lines in slices of `rows_per_chunk`, each with the headings it needs."""
    by_ref = sent_lines(doc)
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
