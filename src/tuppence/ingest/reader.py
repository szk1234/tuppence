"""The AI `read` step: transcribe PDF, OCR and plain-text statements in checked chunks.

Ported from v3's read_with_retry: chunks are read in parallel, each
reply is checked against the lines it cites, and a chunk that fails is sent back
with the errors, at most `max_attempts` times. Only data lines are sent: the
preamble (name, address, account numbers) stays on this device, and the facts
read from it locally are passed as a short summary.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from tuppence.core.errors import safe_error_text
from tuppence.ingest.check import check_rows
from tuppence.ingest.identify import HeaderFacts
from tuppence.ingest.models import (
    CheckLevel,
    Document,
    ParsedRow,
    ParsedStatement,
    Perspective,
    SkippedLine,
)
from tuppence.ingest.prompts import load_prompt
from tuppence.ingest.textnum import to_pence
from tuppence.ingest.textprep import Chunk, plan_chunks, render
from tuppence.llm.types import LLMBadResponse, Message

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
        with self._lock:
            return self._budget.over_cap(estimated_tokens, projected_gbp)

    def check(self, estimated_tokens: int, projected_gbp: float = 0.0) -> None:
        with self._lock:
            self._budget.check(estimated_tokens, projected_gbp)

    def start_call(self) -> None:
        with self._lock:
            self._budget.start_call()

    def record(self, tokens: int, gbp: float | None) -> None:
        with self._lock:
            self._budget.record(tokens, gbp)


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
    """Typed rows from the model's JSON, plus errors for values that don't convert."""
    errors: list[str] = []
    start, end = _iso(out.statement.period_start), _iso(out.statement.period_end)
    if out.statement.period_start and start is None:
        errors.append(f"statement period_start {out.statement.period_start} not ISO")
    if out.statement.period_end and end is None:
        errors.append(f"statement period_end {out.statement.period_end} not ISO")
    parsed = ParsedStatement(
        importer="ai-read",
        perspective=perspective,
        period_start=start,
        period_end=end,
        opening_balance_pence=_pence(out.statement.opening_balance),
        closing_balance_pence=_pence(out.statement.closing_balance),
        currency=out.statement.currency or "GBP",
        skipped=[SkippedLine(ref=s.ref, reason=s.reason) for s in out.skipped],
    )
    for row in out.transactions:
        day = _iso(row.date)
        if day is None:
            errors.append(f"{row.ref}: date {row.date} not ISO")
            continue
        parsed.rows.append(
            ParsedRow(
                ref=row.ref,
                date=day,
                amount_pence=to_pence(Decimal(str(row.amount))),
                amount_text=row.amount_text,
                sign_from=row.sign_from,
                raw_description=row.raw_desc,
                merchant=row.merchant,
                bank_category=row.bank_category,
                bank_type=row.bank_type,
                balance_after_pence=_pence(row.running_balance),
            )
        )
    return parsed, errors


def context_block(*, today: dt.date, account: str, facts: HeaderFacts, level: CheckLevel) -> str:
    lines = [f"TODAY: {today.isoformat()}", f"ACCOUNT: {account}"]
    # Only the period goes to the model. The summary box (opening and closing balance, totals)
    # is withheld like the rest of the preamble; its figures are read on this device.
    header = []
    if facts.period_start and facts.period_end:
        header.append(f"period {facts.period_start.isoformat()} to {facts.period_end.isoformat()}")
    lines.append("STATEMENT HEADER: " + ("; ".join(header) if header else "none"))
    if level == "screenshot":
        lines.append("This is a screenshot from a banking app. Transcribe only what is visible.")
    return "\n".join(lines) + "\n"


def user_message(context: str, chunk: Chunk, doc: Document) -> str:
    by_ref = doc.by_ref()
    heading = [by_ref[r] for r in chunk.context_refs]
    data = [line for line in chunk.lines if line.ref in set(chunk.data_refs)]
    parts = [context]
    if heading:
        parts.append("CONTEXT:\n" + render(heading))
    parts.append("FILE:\n" + render(data))
    return "\n".join(parts)


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
) -> _ChunkOutcome:
    base = user_message(context, chunk, doc)
    errors: list[str] = []
    previous: str | None = None
    last: ParsedStatement | None = None
    max_tokens = min(8192, 600 + OUT_PER_ROW * len(chunk.data_refs))
    for attempt in range(1, max_attempts + 1):
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
            errors, previous = [safe_error_text(exc)], None
            continue
        parsed, conversion = to_parsed(out, perspective=perspective)
        errors = conversion + check_rows(
            chunk.lines,
            all_lines=doc.lines,
            context_refs=chunk.context_refs,
            data_refs=chunk.data_refs,
            parsed=parsed,
            level=level,
        )
        last = parsed
        if not errors:
            return _ChunkOutcome(ok=True, parsed=parsed, errors=[], attempts=attempt)
        previous = out.model_dump_json()
    return _ChunkOutcome(ok=False, parsed=last, errors=errors, attempts=max_attempts)


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
    with ThreadPoolExecutor(max_workers=max(1, min(parallel, len(chunks)))) as pool:
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
                max_attempts=max_attempts,
                context_window=context_window,
            )
            for chunk in chunks
        ]
        outcomes = [f.result() for f in futures]
    parsed = merge([o.parsed for o in outcomes if o.parsed is not None], perspective=perspective)
    errors = [e for o in outcomes for e in o.errors]
    return ReadOutcome(
        ok=not errors and all(o.ok for o in outcomes),
        parsed=parsed,
        errors=errors,
        attempts=max(o.attempts for o in outcomes),
        chunks=len(chunks),
    )
