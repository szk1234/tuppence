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
from tuppence.ingest.clock import Deadline, ticking
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
_ticking = ticking


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
    deadline: Deadline | None = None,
) -> Document:
    tick = _ticking(deadline)
    kept: list[tuple[int, str, list[str]]] = []
    for i, (n, raw, cells) in enumerate(records):
        tick(i)
        if any(c.strip() for c in cells):
            kept.append((n, raw, cells))
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
    return table_document(records, sha256=sha256, kind="csv", known=known, deadline=deadline)


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


# Balance and summary figures are read on this device and never sent. A line with a figure
# and one of these phrases is withheld wherever it is:
# - a balance (never a row, so never reported): opening, closing, start, end, previous or new
#   balance; balance brought or carried forward;
_BALANCE_PHRASE = re.compile(
    r"\b(?:opening|closing|start(?:ing)?|end(?:ing)?|previous|new)\s+balance\b"
    r"|\b(?:brought|carried)\s+forward\b",
    re.IGNORECASE,
)
# - a total: total, money or paid in and out; payments in and out; a total of payments,
#   credits, debits and the like; a summary. A dated line with one may be a row ("CASH PAID IN
#   AT BRANCH"), so it is reported for the person to decide.
_TOTAL_PHRASE = re.compile(
    r"\b(?:total|money|paid)\s+(?:in|out)\b|\bpayments\s+(?:in|out)\b"
    r"|\btotals?\b(?=\s*(?:$|[:£$€(+\-\u2212\d]|(?:money|paid|payments?|credits?|debits?|spent"
    r"|spending|charges|fees|interest|purchases|withdrawals|deposits|in|out|for|this)\b))"
    r"|\b(?:payments?|credits?|debits?|withdrawals|deposits|purchases|fees|charges|interest)"
    r"\s+totals?\b|\b(?:account|statement|balance)\s+summary\b"
    r"|\bsummary\b(?=\s*(?:$|[:£$€\d]|(?:of|for)\b))",
    re.IGNORECASE,
)
# - only a word from those phrases ("BALANCE TRANSFER FEE", "TOTAL FITNESS GYM"): a payee's row
#   is sent like any other when it is a single transaction; anything else is reported.
_SUMMARY_WORD = re.compile(
    r"\b(?:balance|totals?|summary|paid|money|payments?|forward)\b", re.IGNORECASE
)


# Words a balance or a summary is made of. A line with a figure whose words are all from here,
# once figures, dates, markers and punctuation are taken away, is a balance or a summary
# ("Your balance is £1,234.56", "Balance b/f 1,000.00", "You owe £250.00", "Funds available
# £900.00", "Your account is £1,234.56 in credit"), however it is worded.
_SUMMARY_VOCAB = frozenset(
    [
        "balance",
        "bal",
        "balances",
        "forward",
        "brought",
        "carried",
        "b/f",
        "c/f",
        "bf",
        "cf",
        "your",
        "you",
        "is",
        "at",
        "as",
        "on",
        "of",
        "the",
        "close",
        "business",
        "current",
        "available",
        "funds",
        "owe",
        "owed",
        "amount",
        "outstanding",
        "credit",
        "debit",
        "account",
        "in",
        "out",
        "total",
        "totals",
        "money",
        "paid",
        "payments",
        "summary",
        "statement",
        "period",
        "month",
        "new",
        "previous",
        "opening",
        "closing",
        "start",
        "end",
        "this",
        "date",
        "cr",
        "dr",
        "od",
        "overdrawn",
        "spent",
        "spending",
    ]
)
# ...and those of them that make it a balance: never a row, so withheld without a word. A dated
# line of only the others ("02/03/2026 CREDIT 900.00") may be a row: the person decides.
_BALANCE_VOCAB = frozenset(
    [
        "balance",
        "bal",
        "balances",
        "forward",
        "brought",
        "carried",
        "b/f",
        "c/f",
        "bf",
        "cf",
        "owe",
        "owed",
        "outstanding",
        "available",
        "funds",
        "overdrawn",
    ]
)
_WORD = re.compile(r"[a-z]+(?:/[a-z]+)?")
# A pot or a goal ("Savings pot £5,000.00"): its balance, or a transfer to it.
_POT_WORDS = re.compile(
    r"\b(?:savings?|pots?|vaults?|spaces?|jars?|goals?|reserve|isa|saver|round[- ]?ups?)\b",
    re.IGNORECASE,
)


def _summary_words(text: str) -> list[str]:
    """The words left on a line once its figures, dates and punctuation are taken away."""
    plain = _NAMED_DATE.sub(" ", _NUMERIC_DATE.sub(" ", text))  # dates first: "20 November"
    plain = _MONEY_TOKEN.sub(" ", plain)
    plain = re.sub(r"[£$€]\s?[\d,]+(?:\.\d+)?|\d[\d,.]*", " ", plain)
    return _WORD.findall(plain.casefold())


def _pot_balance(text: str) -> bool:
    """An undated line naming only a pot or a goal and its figure ("Savings pot £5,000.00"):
    its balance, or a transfer to it? "TFR TO SAVINGS 50.00" names a transfer, so it is a row."""
    if _has_date(text) or not _POT_WORDS.search(text):
        return False
    words = [w for w in _summary_words(_POT_WORDS.sub(" ", text)) if w not in _SUMMARY_VOCAB]
    return not words


# m1, m2: more words a balance or a card header's payment sentence uses ("Remaining balance",
# "Bal fwd", "Balance on card", "We'll collect your Direct Debit of £25.00 on ..."). They count
# only beside one of `_ANCHORS`, which no transaction description is made of; on their own
# ("DIRECT DEBIT", "CARD PAYMENT", "OVERDRAFT INTEREST") they are a row's words.
_QUALIFIERS = frozenset(
    [
        "after", "transaction", "transactions", "excluding", "including", "pending", "card",
        "today", "to", "pay", "for", "by", "and", "we", "ll", "will", "be", "direct", "debit",
        "payment", "interest", "next", "rate", "arranged", "overdraft", "than", "date", "is",
        "are", "of", "on", "at", "as", "the", "this", "in", "your", "you", "new", "previous",
        "total", "credit", "fwd", "b/fwd", "c/fwd",
    ]
)  # fmt: skip
_ANCHORS = frozenset(
    [
        "balance", "bal", "balances", "forward", "brought", "carried", "b/f", "c/f", "bf", "cf",
        "fwd", "b/fwd", "c/fwd", "owe", "owed", "outstanding", "available", "funds",
        "overdrawn", "remaining", "minimum", "collect", "collected", "due", "please", "avoid",
        "estimated", "statement", "amount", "limit", "spendable", "cleared", "opening",
        "closing", "summary",
    ]
)  # fmt: skip
# A figure in a summary: what is left of the line once dates are taken away.
_FIGURE_TOKEN = re.compile(r"[-+\u2212(]?\s?[£$€]?\d[\d,]*(?:\.\d+)?\)?")


_BALANCE_WORDING = _SUMMARY_VOCAB | _QUALIFIERS | _ANCHORS
# R1 (re-review 2): words that make a line of balance wording a sentence about what is due or
# owed ("Your minimum payment of £25.00 is due by ...", "Pay by ... to avoid interest on ...",
# "Interest rate ...") rather than a row's description.
_SENTENCE = frozenset(
    ["is", "are", "will", "be", "we", "ll", "please", "avoid", "collect", "collected", "by",
     "to", "next", "estimated", "rate"]
)  # fmt: skip
# The words of a balance itself, however it is printed (dated, or signed when overdrawn):
# "01 Oct BALANCE B/F 1,000.00", "Balance -£250.00".
_BALANCE_ONLY = frozenset(
    ["balance", "bal", "balances", "forward", "brought", "carried", "b/f", "c/f", "bf", "cf",
     "fwd", "b/fwd", "c/fwd", "opening", "closing", "previous", "new", "start", "starting",
     "end", "current", "available", "cleared", "funds", "remaining", "outstanding", "overdrawn",
     "owed", "spendable", "limit", "statement", "account", "your", "the", "of", "on", "at", "as",
     "this", "after", "today", "pending", "excluding", "including", "cr", "dr", "od", "close",
     "business", "date"]
)  # fmt: skip


_ROW_HEADS = frozenset(["payment", "credit", "debit"])


def _names_a_row(words: Sequence[str]) -> bool:
    """Balance wording that names a transaction: interest, or a payment, a credit or a debit
    ("INTEREST ON CREDIT BALANCE", "CLOSING INTEREST", "STATEMENT CREDIT", "PAYMENT OF MINIMUM
    AMOUNT DUE", "MINIMUM PAYMENT DIRECT DEBIT"), and not a sentence about what is due."""
    if _SENTENCE.intersection(words):
        return False
    return "interest" in words or words[0] == "payment" or words[-1] in _ROW_HEADS


def _balance_only(words: Sequence[str]) -> bool:
    return bool(words) and _BALANCE_ONLY.issuperset(words)


def _labelled(text: str) -> bool:
    """Every figure has its own label: no figure has a sign (+£25.00 is a row's), and no two
    figures sit side by side (an amount and a balance are a row's)."""
    plain = _NAMED_DATE.sub(" ", _NUMERIC_DATE.sub(" ", text))
    previous: int | None = None
    for match in _FIGURE_TOKEN.finditer(plain):
        if match.group(0)[0] in "-+\u2212":
            return False
        if previous is not None and re.search(r"[A-Za-z]", plain[previous : match.start()]) is None:
            return False
        previous = match.end()
    return True


def _summary_kind(text: str) -> str | None:
    """For a line with an amount, what its summary wording makes it (R-M3-23 (e)):

    - "balance": a pure balance or summary line, nothing on it but balance or summary wording
      and its figures (a balance in any wording; a card header's payment sentence; a total
      with each figure under its own label). Withheld, read here, never a row: not reported.
    - "held": summary wording that may be a row's ("CREDIT 900.00 1,900.00", "PAID IN AT POST
      OFFICE", "Money in +£25.00", a pot). Withheld and reported for the person to decide.
    - None: a row's line like any other.

    A line of balance wording is a balance only when nothing on it says it is a row (R1): it
    names no transaction (`_names_a_row`), and it has no date in front and no signed figure or
    figure pair, unless all its words are a balance's own ("01 Oct BALANCE B/F 1,000.00")."""
    words = _summary_words(text)
    if _BALANCE_PHRASE.search(text) or (
        sensitive.is_balance_line(text) and (_labelled(text) or _balance_only(words))
    ):
        return "balance"
    if (
        words
        and all(w in _BALANCE_WORDING for w in words)
        and any(w in _ANCHORS for w in words)
        and not _names_a_row(words)
        and (_balance_only(words) or (_DATE_FIRST.match(text) is None and _labelled(text)))
    ):
        return "balance"
    dated = _has_date(text)
    if words and all(w in _SUMMARY_VOCAB for w in words):
        if not dated and ({"your", "you"} & set(words)):  # "Your account is £1.00 in credit"
            return "balance"
        if not dated and _TOTAL_PHRASE.search(text) and _labelled(text):
            return "balance"
        return "held"
    if _pot_balance(text) or _TOTAL_PHRASE.search(text):
        return "held"
    return None


def pure_balance(text: str, *, names: Sequence[str] = ()) -> bool:
    """A line with an amount that is only a balance or a summary (`_summary_kind`), or a
    balance line with an account detail on it: the only lines with an amount that are withheld
    without being reported."""
    return _summary_kind(text) == "balance" or _masked_balance(text, names)


def _masked_balance(text: str, names: Sequence[str]) -> bool:
    """A balance line with an account detail on it (`sensitive.is_masked_balance`). A line
    that is a balance line as printed is one only as `_summary_kind` reads it: "MINIMUM PAYMENT
    -£25.00" has a row's sign."""
    if sensitive.is_balance_line(text):
        return _summary_kind(text) == "balance"
    return sensitive.is_masked_balance(text, names=names)


def only_summary_vocab(text: str) -> bool:
    """A line made only of balance or summary words (and figures, dates, markers)."""
    words = _summary_words(text)
    return bool(words) and all(word in _SUMMARY_VOCAB for word in words)


def _single_transaction(text: str, row: bool) -> bool:
    """A row of the table: dated, or a description with its amount (and at most a balance)."""
    return row or (not _has_date(text) and len(_MONEY_TOKEN.findall(text)) <= 2)


_HEADING_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in _HEADING_WORDS) + r")\b")


def is_heading(text: str) -> bool:
    lowered = text.casefold()
    return (
        len(set(_HEADING_RE.findall(lowered))) >= 2
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
# A row of the table starts with its date ("01 Oct 2026 ...", "Mon 5 Oct ..."); a summary line
# in the header ("Please pay £25.00 by 20/11/2026") prints it later.
_DATE_FIRST = re.compile(
    rf"^\s*(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s+)?(?:\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}\b"
    rf"|\d{{4}}-\d{{2}}-\d{{2}}\b|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\b)",
    re.IGNORECASE,
)


def _first_run(
    rows: Sequence[bool],
    leading: Sequence[bool],
    lo: int,
    hi: int,
    header: Sequence[bool] | None = None,
) -> int | None:
    """The first of two row-shaped lines at most `RUN_GAP` apart, both starting with their date
    if any such pair exists, else any pair.

    A run followed by the header's own lines (`header`: the holder's name and address) before
    the next row is the header's (re-review 2 R8): the next run is the table, if there is one."""
    shaped = [i for i in range(lo, hi) if rows[i]]
    after = dict(zip(shaped, [*shaped[1:], hi], strict=False))
    pairs = [(a, b) for a, b in zip(shaped, shaped[1:], strict=False) if b - a <= RUN_GAP]

    def above_header(b: int) -> bool:
        return header is not None and any(header[k] for k in range(b + 1, after[b]))

    for group in ([(a, b) for a, b in pairs if leading[a] and leading[b]], pairs):
        if group:
            return next((a for a, b in group if not above_header(b)), group[0][0])
    return None


def _table_start(
    rows: Sequence[bool],
    headings: Sequence[bool],
    lo: int,
    hi: int,
    *,
    leading: Sequence[bool] | None = None,
    pages: Sequence[str | None] | None = None,
    header: Sequence[bool] | None = None,
) -> int | None:
    """Where the transactions start among lines `lo`..`hi` (R-M3-23 (b)): the column-heading
    row whenever there is one; else the first run of rows (two rows at most `RUN_GAP` apart,
    rows that start with their date first, not followed by the holder's name and address);
    else the first row.

    A run comes before the heading row only when the headings are first printed on a later
    page: the rows on the pages before it are the table. On the heading's own page, lines above
    it are the header, however row-shaped (a summary box merged onto the address rows)."""
    leading = leading if leading is not None else [True] * len(rows)
    heading = next((i for i in range(lo, hi) if headings[i]), None)
    run = _first_run(rows, leading, lo, hi, header)
    if heading is not None:
        if (
            run is not None
            and run < heading
            and pages is not None
            and pages[run] is not None
            and pages[run] != pages[heading]
        ):
            return run
        return heading
    if run is not None:
        return run
    return next((i for i in range(lo, hi) if rows[i]), None)


# A line that is part of an address without being one on its own: a house or building name, or
# a street word. "Exampletown" alone is not; in a block with a postcode or two of these, it is.
_ADDRESS_WORD = re.compile(
    r"\b(?:house|cottage|farm|lodge|mill|manor|court|mansions|building|barn|hall|villas?|"
    r"road|street|lane|avenue|close|drive|gardens|place|terrace|crescent|square|grove|mews|"
    r"walk|way|row|green|park|rise|view|hill)\b",
    re.IGNORECASE,
)


_HOLDER_PREFIX = re.compile(
    r"^(?:(?:statement|prepared)\s+for|name\s*:|account\s+holders?\s*:?|joint\s+account\s*:"
    r"|holder\s*:?)\s*",
    re.IGNORECASE,
)
_TITLE_WORD = re.compile(r"^(?:mr|mrs|ms|miss|mx|dr|prof)\.?$", re.IGNORECASE)
_NAME_PART = re.compile(r"[A-Za-z][A-Za-z'.-]*")


def header_names(texts: Sequence[str], rows: Sequence[int]) -> list[str]:
    """The holders' names printed in a statement's header (lines `rows` of `texts`): a line
    with a title or "Statement for", or a line that is only a name. "MR ALEX EXAMPLE & MRS PAT
    EXAMPLE" gives both. They are masked wherever they appear in the rows, like the household's
    own names."""
    found: list[str] = []
    for i in rows:
        text = sensitive.normalise(texts[i])
        if "holder_name" not in sensitive.classify(text) and sensitive.name_key(text) is None:
            continue
        body = _HOLDER_PREFIX.sub("", text)
        for part in re.split(r"\s*(?:&|\band\b|,)\s*", body, flags=re.IGNORECASE):
            words = [w for w in part.split() if not _TITLE_WORD.fullmatch(w)]
            if 2 <= len(words) <= 4 and all(_NAME_PART.fullmatch(w) for w in words):
                found.append(" ".join(words))
    return list(dict.fromkeys(found))


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
    postcode or two of them address parts (a house or street, or the holder's name with a
    title). Every line of the block is withheld, the town and a house name too (they aren't
    sensitive on their own)."""
    postcode = sensitive.VALUES["postcode"]
    address = sensitive.VALUES["address"]
    holder = sensitive.VALUES["holder_name"]  # "MR ALEX EXAMPLE" heads an address (R8)
    found: set[int] = set()
    i = 0
    while i < len(texts):
        j = i
        while j < len(texts) and _plain_short(texts[j]):
            j += 1
        block = range(i, j)
        if len(block) >= 2 and (
            any(postcode.search(texts[k]) for k in block)
            or sum(
                1
                for k in block
                if address.search(texts[k])
                or _ADDRESS_WORD.search(texts[k])
                or holder.search(texts[k])
            )
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
    leading: Sequence[bool],
    header: Sequence[bool] | None = None,
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
        table = _table_start(rows, headings, lo, hi, leading=leading, header=header)
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
    """`sensitive.prepare_outbound` once per line: what is sent for it, or None (withheld)."""

    def __init__(self, texts: Sequence[str], names: Sequence[str]) -> None:
        self.texts, self.names = texts, names
        self.done: dict[int, MaskedLine | None] = {}

    def __call__(self, i: int) -> MaskedLine | None:
        if i not in self.done:
            self.done[i] = _prepare(self.texts, i, self.names)
        return self.done[i]


def _prepare(texts: Sequence[str], i: int, names: Sequence[str]) -> MaskedLine | None:
    """`sensitive.prepare_outbound` for line `i`, with the lines printed above and below it in
    view (a number a line wrap splits is masked in each part)."""
    return sensitive.prepare_outbound(
        texts[i],
        names=names,
        before=texts[i - 1] if i > 0 else None,
        after=texts[i + 1] if i + 1 < len(texts) else None,
    )


def _send(out: _Split, line: Line, prepared: MaskedLine | None) -> bool:
    """Make `line` data, sent as `prepared` (from `sensitive.prepare_outbound`); False when it
    can't be sent."""
    if prepared is None:
        return False
    out.data.append(line.ref)
    if prepared.hidden or prepared.text != line.text:
        out.masked[line.ref] = prepared
    return True


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
    amounts = [has_amount(t) for t in texts]
    kinds = [_summary_kind(t) if amounts[i] else None for i, t in enumerate(texts)]
    rows = [
        _has_date(t)
        and _MONEY_TOKEN.search(t) is not None
        and not is_summary(t)
        and not _BALANCE_PHRASE.search(t)
        and kinds[i] is None  # a summary line, pure or one that may be a row, is no run (R8)
        and (not sensitive_at[i] or masks(i) is not None)
        for i, t in enumerate(texts)
    ]
    headings = [is_heading(t) for t in texts]
    leading = [_DATE_FIRST.match(t) is not None for t in texts]
    pages = [m.group(1) if (m := _PAGE_REF.fullmatch(line.ref)) else None for line in lines]
    addresses = _address_blocks(texts)
    header = [i in addresses for i in range(len(texts))]
    first = _table_start(rows, headings, 0, len(lines), leading=leading, pages=pages, header=header)
    edge = _page_edge_repeats(lines)
    blocks = _balance_blocks(texts)
    balances = {k for block in blocks for k in block}
    known_names = {k for n in names if (k := _name_key(n))}
    for line in lines[: first if first is not None else 0]:
        if key := _name_key(line.text):
            known_names.add(key)
    # The holders printed above the table are masked in its rows like the household's names.
    printed = header_names(texts, [i for i in range(first or 0) if i in addresses or i < 12])
    if printed:
        names = [*names, *printed]
        sensitive_at = [is_sensitive(t, names=names) for t in texts]
        masks = _Masks(texts, names)
    page_headers = _later_page_headers(lines, first, rows, headings, leading, header)
    out = _Split()
    unsure: set[int] = set()

    def pure(i: int) -> bool:  # only a balance or a summary: never a row, so not reported
        return kinds[i] == "balance" or (sensitive_at[i] and _masked_balance(texts[i], names))

    for i, line in enumerate(lines):
        tick(i)
        text = line.text
        key = _normal(text)
        money = _MONEY_TOKEN.search(text) is not None
        amount = amounts[i]
        furniture = (
            counts[key] > 1
            and not amount
            and not is_heading(text)
            and (_FURNITURE.search(text) is not None or key in edge)
        )
        plain = not amount and not _has_date(text)
        name_repeat = plain and _name_key(text) in known_names and _name_key(text) is not None
        if first is not None and i < first:
            out.withheld.append(line.ref)
            if amount and _has_date(text) and not pure(i):
                unsure.add(i)  # a dated amount above the table start may be a row: reported
        elif i in balances or pure(i):
            out.withheld.append(line.ref)  # a balance or a summary: read here, never a row
        elif kinds[i] == "held":
            out.withheld.append(line.ref)  # a summary or a row? the person decides
            unsure.add(i)
        elif i in page_headers:
            out.withheld.append(line.ref)
            if amount:
                unsure.add(i)  # may be a row above the page's first anchor
        elif i in addresses or furniture or name_repeat or (sensitive_at[i] and not amount):
            out.withheld.append(line.ref)  # no amount on it, so no row is lost
        elif (
            money and _SUMMARY_WORD.search(text) and not _single_transaction(text, rows[i])
        ) or not _send(out, line, masks(i)):
            out.withheld.append(line.ref)  # a total or a row? or details that won't mask:
            unsure.add(i)  # the person decides
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
    # The same summary rule as text and PDFs (R-M3-23 (e)): a pure balance or summary is never
    # sent nor reported; one that may be a row is withheld and reported. A balance line without
    # an amount ("Balance 1234") has nothing to report.
    kinds = [_summary_kind(t) if has_amount(t) else None for t in texts]
    balances |= {
        i for i, t in enumerate(texts) if not has_amount(t) and sensitive.is_balance_line(t)
    }
    balances |= {i for i, kind in enumerate(kinds) if kind == "balance"}
    summaries = {i for i, kind in enumerate(kinds) if kind == "held"}
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
    names = [*names, *header_names(texts, range(min(first, len(texts))))]
    addresses = _address_blocks(texts)  # wherever they are (R-M3-23 (b))
    out = _Split()
    for i, line in enumerate(lines):
        tick(i)
        text = line.text
        if i in addresses:  # short plain lines: no amount on them, so no row is lost
            out.withheld.append(line.ref)
            continue
        detail = is_sensitive(text, names=names)
        if (
            i >= first
            and i not in balances
            and detail
            and _name_key(text) is None
            and has_amount(text)
            and _masked_balance(text, names)
        ):
            balances.add(i)  # a balance with a detail on it: withheld, not reported
        if (
            i >= first
            and i not in balances
            and i not in summaries
            and _name_key(text) is None
            and (not detail or has_amount(text))
            and _send(out, line, _prepare(texts, i, names))
        ):
            continue  # sent (a row with account details has them masked)
        out.withheld.append(line.ref)
        if has_amount(text) and (i not in balances or i in unsure):
            out.held.append(line.ref)
    return out


def _capped(ref: str, text: str, too_long: list[str]) -> Line:
    """A line as it is kept, read and sent: folded (`sensitive.normalise`: compatibility forms,
    invisible characters, curly apostrophes and odd spaces), then cut short (and listed in
    `too_long`) when it is over the cap."""
    if len(text) <= 4 * MAX_LINE_CHARS:  # a longer line is never read, so isn't folded
        text = sensitive.normalise(text)
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


def plan_chunks(
    doc: Document, *, rows_per_chunk: int, deadline: Deadline | None = None
) -> list[Chunk]:
    """Data lines in slices of `rows_per_chunk`, each with the headings it needs."""
    tick = _ticking(deadline)
    by_ref = sent_lines(doc)
    order = {line.ref: i for i, line in enumerate(doc.lines)}
    data = [by_ref[r] for r in doc.data_refs]
    step = max(1, rows_per_chunk)
    # last heading-like data line before each slice start, found in one pass
    last_heading: dict[int, Line | None] = {}
    current: Line | None = None
    for i, line in enumerate(data):
        tick(i)
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
