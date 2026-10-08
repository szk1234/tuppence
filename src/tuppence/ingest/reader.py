"""The AI `read` step: transcribe PDF, OCR and plain-text statements in checked chunks.

Ported from v3's read_with_retry: chunks are read in parallel, each
reply is checked against the lines it cites, and a chunk that fails is sent back
with the errors, at most `max_attempts` times. Only data lines are sent: the
preamble (name, address, account numbers) stays on this device, and the facts
read from it locally are passed as a short summary.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
from collections.abc import Sequence
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from decimal import Decimal
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from tuppence.core.errors import safe_error_text
from tuppence.ingest.check import check_rows, drop_unprinted_balances
from tuppence.ingest.identify import HeaderFacts
from tuppence.ingest.models import (
    CheckLevel,
    Document,
    Line,
    ParsedRow,
    ParsedStatement,
    Perspective,
    SkippedLine,
)
from tuppence.ingest.prompts import load_prompt
from tuppence.ingest.textnum import to_pence
from tuppence.ingest.textprep import Chunk, plan_chunks, render
from tuppence.llm.types import BudgetExceeded, LLMBadResponse, Message

MAX_ERROR_LINES = 40
PROMPT_TOKENS = 1700  # the read prompt plus the JSON-schema instruction, roughly
IN_PER_ROW = 30  # tokens for one statement line
OUT_PER_ROW = 80  # tokens for one transaction in the reply


# A reply with NaN, Infinity or an absurd figure fails validation, so it is retried
# like any other bad answer instead of raising later.
Money = Annotated[float, Field(ge=-1e9, le=1e9)]


class ReadStatementOut(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    period_start: str | None
    period_end: str | None
    opening_balance: Money | None
    closing_balance: Money | None
    currency: str | None


class ReadRowOut(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    ref: str
    date: str
    amount: Money
    amount_text: str
    sign_from: str | None
    raw_desc: str
    merchant: str | None
    bank_category: str | None
    bank_type: str | None
    running_balance: Money | None


class ReadSkipOut(BaseModel):
    ref: str
    reason: str


class ReadOut(BaseModel):
    statement: ReadStatementOut
    transactions: list[ReadRowOut]
    skipped: list[ReadSkipOut]


class StructuredLLM(Protocol):
    def structured(
        self,
        task: str,
        messages: Sequence[Message],
        schema: type[Any],
        *,
        max_tokens: int = 4096,
        run: Any = None,
    ) -> Any: ...


class ReadOutcome(BaseModel):
    ok: bool
    parsed: ParsedStatement  # best effort even when not ok: the fix-up screen starts from it
    errors: list[str] = Field(default_factory=list)
    attempts: int = 0
    chunks: int = 0


class LockedBudget:
    """Wraps a RunBudget so parallel chunks can share it: every RunBudget method and limit the
    LLM client uses, each under one lock."""

    def __init__(self, budget: Any) -> None:
        self._budget, self._lock = budget, threading.RLock()
        self._changed = threading.Condition(self._lock)  # a hold was recorded or dropped
        self._held: dict[int, tuple[int, float]] = {}

    @property
    def max_seconds(self) -> float:
        return self._budget.max_seconds

    @property
    def calls(self) -> int:
        return self._budget.calls

    @property
    def tokens(self) -> int:
        return self._budget.tokens

    @property
    def gbp(self) -> float:
        return self._budget.gbp

    def remaining_seconds(self) -> float:
        with self._lock:
            return self._budget.remaining_seconds()

    def check_time(self) -> None:
        with self._lock:
            self._budget.check_time()

    def check_limits(self) -> None:
        with self._lock:
            self._budget.check_limits()

    def over_cap(self, estimated_tokens: int, projected_gbp: float) -> str | None:
        """Why this call doesn't fit. When it does fit, its estimate is held for this thread
        until it records its usage, so parallel calls can't each pass the check and then
        together overshoot the token or £ cap.

        When the call would fit on its own and only other calls' holds are in the way, it
        waits for them to finish and looks again, so the chunks are then read one at a time
        instead of the read ending (or the chunk going to another model)."""
        me = threading.get_ident()
        with self._changed:
            self._drop(me)
            while True:
                others_tokens = sum(t for t, _ in self._held.values())
                others_gbp = sum(g for _, g in self._held.values())
                reason = self._budget.over_cap(
                    estimated_tokens + others_tokens, projected_gbp + others_gbp
                )
                if reason is None:
                    self._held[me] = (estimated_tokens, projected_gbp)
                    return None
                alone = self._budget.over_cap(estimated_tokens, projected_gbp)
                left = self._budget.remaining_seconds()
                if not self._held or alone is not None or left <= 0:
                    return reason  # a real refusal: it doesn't fit even by itself
                self._changed.wait(timeout=min(left, 1.0))

    def _drop(self, thread: int) -> None:
        if self._held.pop(thread, None) is not None:
            self._changed.notify_all()

    def check(self, estimated_tokens: int, projected_gbp: float = 0.0) -> None:
        with self._lock:
            self._budget.check_limits()
        reason = self.over_cap(estimated_tokens, projected_gbp)
        if reason:
            raise BudgetExceeded(reason)

    def start_call(self) -> None:
        with self._lock:
            self._budget.start_call()

    def record(self, tokens: int, gbp: float | None) -> None:
        with self._changed:
            self._budget.record(tokens, gbp)
            self._drop(threading.get_ident())

    def release(self) -> None:
        """Drop this thread's hold (a call that failed and recorded nothing)."""
        with self._changed:
            self._drop(threading.get_ident())


def rows_per_chunk_for(context_window: int, configured: int) -> int:
    """Fit a chunk, its reply and the prompt inside the model's context window (spec §10.3)."""
    fits = (context_window - PROMPT_TOKENS - 600) // (IN_PER_ROW + OUT_PER_ROW)
    return max(5, min(configured, fits))


def retry_message(
    base: str, errors: Sequence[str], previous: str | None, *, context_window: int
) -> str:
    """The chunk again with what failed, sized to the 60% input share of the model's context
    window: as many errors as fit (always the first three), then the previous answer if it fits."""
    budget_chars = int(context_window * 0.6 - PROMPT_TOKENS) * 4
    message = f"{base}\n\nYour previous answer failed verification:"
    for i, error in enumerate(errors[:MAX_ERROR_LINES]):
        if i >= 3 and len(message) + len(error) + 1 > budget_chars:
            break
        message += f"\n{error}"
    if previous and len(message) + len(previous) + 1 <= budget_chars:
        message += f"\n{previous}"
    return message


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_REF = re.compile(r"(?<![?\w])(?:P\d+)?L\d+\b")
UNKNOWN = "?"  # marks a ref the model made up, so it can never name a line of the file


def tidy(text: str, limit: int = 200) -> str:
    """Text that came from a model, made safe to store: no control characters, capped."""
    return _CONTROL.sub(" ", text)[:limit]


def ref_aliases(doc: Document) -> dict[str, str]:
    """Dense opaque ids (D1, D2, ...) for the lines that may be sent, in file order, so the
    model never sees where withheld lines sit between them."""
    sendable = {*doc.data_refs, *doc.header_refs}
    return {
        line.ref: f"D{n}"
        for n, line in enumerate((ln for ln in doc.lines if ln.ref in sendable), start=1)
    }


def _in_aliases(text: str, aliases: dict[str, str]) -> str:
    return _REF.sub(lambda m: aliases.get(m.group(0), m.group(0)), text)


def _iso(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _pence(value: float | None) -> int | None:
    return None if value is None else to_pence(Decimal(str(value)))


def to_parsed(out: ReadOut, *, perspective: Perspective) -> tuple[ParsedStatement, list[str]]:
    """Typed rows from the model's JSON, plus errors for values that don't convert. Every
    string the model wrote is kept without control characters and capped at 200."""
    errors: list[str] = []
    start, end = _iso(out.statement.period_start), _iso(out.statement.period_end)
    if out.statement.period_start and start is None:
        errors.append(f"statement period_start {tidy(out.statement.period_start, 40)} not ISO")
    if out.statement.period_end and end is None:
        errors.append(f"statement period_end {tidy(out.statement.period_end, 40)} not ISO")
    currency = (out.statement.currency or "GBP").strip().upper()
    if currency != "GBP":
        errors.append(f"statement currency is {tidy(currency, 8)}, not GBP")
    parsed = ParsedStatement(
        importer="ai-read",
        perspective=perspective,
        period_start=start,
        period_end=end,
        opening_balance_pence=_pence(out.statement.opening_balance),
        closing_balance_pence=_pence(out.statement.closing_balance),
        currency="GBP",
        skipped=[SkippedLine(ref=tidy(s.ref, 60), reason=tidy(s.reason)) for s in out.skipped],
    )
    for row in out.transactions:
        day = _iso(row.date)
        if day is None:
            errors.append(f"{tidy(row.ref, 60)}: date {tidy(row.date, 40)} not ISO")
            continue
        parsed.rows.append(
            ParsedRow(
                ref=tidy(row.ref, 60),
                date=day,
                amount_pence=to_pence(Decimal(str(row.amount))),
                amount_text=tidy(row.amount_text),
                sign_from=_tidy(row.sign_from),
                raw_description=tidy(row.raw_desc),
                merchant=_tidy(row.merchant),
                bank_category=_tidy(row.bank_category),
                bank_type=_tidy(row.bank_type),
                balance_after_pence=_pence(row.running_balance),
            )
        )
    return parsed, errors


def _tidy(text: str | None) -> str | None:
    return None if text is None else tidy(text)


def context_block(*, today: dt.date, account: str, facts: HeaderFacts, level: CheckLevel) -> str:
    lines = [f"TODAY: {today.isoformat()}", f"ACCOUNT: {account}"]
    # Only the period goes to the model. The summary box (opening and closing balance, totals)
    # is withheld like the rest of the preamble; its figures are read on this device.
    header = []
    if facts.period_start and facts.period_end:
        header.append(f"period {facts.period_start.isoformat()} to {facts.period_end.isoformat()}")
    lines.append("STATEMENT HEADER: " + ("; ".join(header) if header else "none"))
    if level == "screenshot":
        lines.append(
            "This is a screenshot from a banking app. Transcribe only what is visible. A row "
            "with an amount but no date of its own is still a transaction: it takes the date "
            "of the heading above it (Today is TODAY, Yesterday the day before), and a "
            "pending row takes TODAY."
        )
    return "\n".join(lines) + "\n"


def user_message(
    context: str, chunk: Chunk, doc: Document, aliases: dict[str, str] | None = None
) -> str:
    ids = aliases if aliases is not None else ref_aliases(doc)
    # The chunk's own lines, as they may be sent: account details in a row are masked.
    in_chunk = {line.ref: line for line in chunk.lines}
    heading = [in_chunk[r] for r in chunk.context_refs if r in in_chunk]
    data_set = set(chunk.data_refs)
    data = [line for line in chunk.lines if line.ref in data_set]
    parts = [context]
    if heading:
        parts.append("CONTEXT:\n" + render(_renamed(heading, ids)))
    parts.append("FILE:\n" + render(_renamed(data, ids)))
    return "\n".join(parts)


def _renamed(lines: Sequence[Line], ids: dict[str, str]) -> list[Line]:
    return [Line(ref=ids[line.ref], text=line.text) for line in lines]


class _ChunkOutcome(BaseModel):
    ok: bool
    parsed: ParsedStatement | None
    errors: list[str]
    attempts: int


def _read_chunk(
    doc: Document,
    chunk: Chunk,
    *,
    llm: StructuredLLM,
    run: Any,
    prompt: str,
    context: str,
    perspective: Perspective,
    level: CheckLevel,
    max_attempts: int,
    context_window: int,
    aliases: dict[str, str],
    stop: threading.Event,
) -> _ChunkOutcome:
    """Read one chunk. The reply is checked against the lines that were sent, and nothing
    else, so the feedback can't tell the model anything about the lines that were withheld;
    the whole-file check runs once, afterwards, on this device."""
    base = user_message(context, chunk, doc, aliases)
    real = {alias: ref for ref, alias in aliases.items()}
    errors: list[str] = []
    previous: str | None = None
    last: ParsedStatement | None = None
    done = 0
    max_tokens = min(8192, 600 + OUT_PER_ROW * len(chunk.data_refs))
    try:
        for attempt in range(1, max_attempts + 1):
            if stop.is_set():
                break
            done = attempt
            user = (
                base
                if attempt == 1
                else retry_message(base, errors, previous, context_window=context_window)
            )
            try:
                out = llm.structured(
                    "read",
                    [Message(role="system", content=prompt), Message(role="user", content=user)],
                    ReadOut,
                    max_tokens=max_tokens,
                    run=run,
                )
            except LLMBadResponse as exc:
                errors, previous = [tidy(safe_error_text(exc))], None
                continue
            parsed, conversion = to_parsed(_with_real_refs(out, real), perspective=perspective)
            # A running balance counts only if it is printed on the row's own (sent) line.
            drop_unprinted_balances(parsed, chunk.lines)
            checked = conversion + check_rows(
                chunk.lines,
                all_lines=chunk.lines,
                context_refs=chunk.context_refs,
                data_refs=chunk.data_refs,
                parsed=parsed,
                level=level,
            )
            errors = [tidy(_in_aliases(e, aliases)) for e in checked]
            last = parsed
            if not errors:
                return _ChunkOutcome(ok=True, parsed=parsed, errors=[], attempts=attempt)
            previous = out.model_dump_json()
    except BaseException:
        stop.set()  # a fatal error ends the whole read: no other chunk starts a new call
        raise
    finally:
        if isinstance(run, LockedBudget):
            run.release()
    return _ChunkOutcome(ok=False, parsed=last, errors=errors, attempts=done)


def _with_real_refs(out: ReadOut, real: dict[str, str]) -> ReadOut:
    """The reply with the model's ids turned back into the file's own refs. Only an id the
    model was given counts: any other (one it made up, a guess at a file ref such as "P1L2",
    or an id with a suffix such as "D2#fee") is marked unknown, so it names no line and is
    reported as a ref that isn't in the chunk."""

    def known(ref: str) -> str:
        return real.get(ref) or f"{UNKNOWN}{ref}"

    return ReadOut(
        statement=out.statement,
        transactions=[row.model_copy(update={"ref": known(row.ref)}) for row in out.transactions],
        skipped=[skip.model_copy(update={"ref": known(skip.ref)}) for skip in out.skipped],
    )


def merge(parts: Sequence[ParsedStatement], *, perspective: Perspective) -> ParsedStatement:
    merged = ParsedStatement(importer="ai-read", perspective=perspective)
    starts = [p.period_start for p in parts if p.period_start]
    ends = [p.period_end for p in parts if p.period_end]
    merged.period_start = min(starts) if starts else None
    merged.period_end = max(ends) if ends else None
    merged.opening_balance_pence = next(
        (p.opening_balance_pence for p in parts if p.opening_balance_pence is not None), None
    )
    merged.closing_balance_pence = next(
        (p.closing_balance_pence for p in reversed(parts) if p.closing_balance_pence is not None),
        None,
    )
    merged.currency = parts[0].currency if parts else "GBP"
    for p in parts:
        merged.rows.extend(p.rows)
        merged.skipped.extend(p.skipped)
    return merged


def read_document(
    doc: Document,
    *,
    llm: StructuredLLM,
    run: Any,
    perspective: Perspective,
    level: CheckLevel,
    account: str,
    facts: HeaderFacts,
    today: dt.date,
    context_window: int,
    rows_per_chunk: int = 40,
    max_attempts: int = 3,
    parallel: int = 2,
    prompt: str | None = None,
) -> ReadOutcome:
    chunks = plan_chunks(doc, rows_per_chunk=rows_per_chunk_for(context_window, rows_per_chunk))
    empty = ParsedStatement(importer="ai-read", perspective=perspective)
    if not chunks:
        return ReadOutcome(
            ok=False, parsed=empty, errors=["No transaction lines were found in this file."]
        )
    shared = LockedBudget(run) if run is not None else None
    text = prompt or load_prompt("read")
    context = context_block(today=today, account=account, facts=facts, level=level)
    aliases = ref_aliases(doc)
    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=max(1, min(parallel, len(chunks))))
    futures = [
        pool.submit(
            _read_chunk,
            doc,
            chunk,
            llm=llm,
            run=shared,
            prompt=text,
            context=context,
            perspective=perspective,
            level=level,
            max_attempts=max(1, max_attempts),
            context_window=context_window,
            aliases=aliases,
            stop=stop,
        )
        for chunk in chunks
    ]
    wait(futures, return_when=FIRST_EXCEPTION)
    failed = next((f for f in futures if f.done() and f.exception() is not None), None)
    if failed is not None:  # a fatal error ends the read: nothing queued is sent
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
        error = failed.exception()
        assert error is not None
        raise error
    pool.shutdown()
    outcomes = [f.result() for f in futures]
    parsed = merge([o.parsed for o in outcomes if o.parsed is not None], perspective=perspective)
    real = {alias: ref for ref, alias in aliases.items()}
    errors = [
        tidy(re.sub(r"\bD\d+\b", lambda m: real.get(m.group(0), m.group(0)), e))
        for o in outcomes
        for e in o.errors
    ]
    return ReadOutcome(
        ok=not errors and all(o.ok for o in outcomes),
        parsed=parsed,
        errors=errors,
        attempts=max(o.attempts for o in outcomes),
        chunks=len(chunks),
    )
