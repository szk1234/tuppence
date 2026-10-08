"""Step 3 of the pipeline: from a Document to checked rows (spec §6.2 steps 3–4).

Fixed importers first; the AI only for an unfamiliar CSV layout (once) and for
PDF, OCR and plain-text statements. Used by the ingest graph and the eval harness.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tuppence.core.errors import safe_error_text
from tuppence.ingest import verify
from tuppence.ingest.clock import after
from tuppence.ingest.extract import ExtractLimits, read_camt
from tuppence.ingest.identify import Evidence
from tuppence.ingest.importers.camt import CamtError, parsed_from_facts
from tuppence.ingest.importers.csv_layout import LayoutMismatch, parse_with_layout
from tuppence.ingest.importers.ofx import OfxError, parse_ofx
from tuppence.ingest.importers.qif import QifError, parse_qif
from tuppence.ingest.mapping import propose_layout
from tuppence.ingest.models import (
    AccountKind,
    CheckLevel,
    Document,
    ParsedStatement,
)
from tuppence.ingest.prompts import load_prompt
from tuppence.ingest.reader import StructuredLLM, read_document, tidy
from tuppence.ingest.registry import CsvLayout, LayoutRegistry
from tuppence.ingest.textnum import decode_text
from tuppence.ingest.verify import (  # noqa: F401 - parse's own names for them
    Basis,
    card_payment,
    held_back_message,
    kind_doubt,
    sign_doubt,
    too_long_message,
)

ACCOUNT_LABELS: dict[str, str] = {
    "current": "current account",
    "savings": "savings account",
    "credit_card": "credit card (the statement prints purchases as positive figures)",
}


LOCAL_SECONDS = 180.0  # this device's own work on one statement, a pass at a time


class ReaderLimits(BaseModel):
    max_attempts_per_chunk: int = Field(default=3, ge=1, le=3)
    rows_per_chunk: int = Field(default=40, ge=1, le=200)
    parallel_chunks: int = Field(default=2, ge=1, le=8)


class ParseOutcome(BaseModel):
    parsed: ParsedStatement
    errors: list[str] = Field(default_factory=list)
    level: CheckLevel = "full"
    info: dict[str, Any] = Field(default_factory=dict)
    # A newly proposed CSV layout that read the file but whose signs can't be confirmed yet.
    # It is not saved; the caller may save it once the person has confirmed the statement.
    pending_layout: CsvLayout | None = None
    # A newly learned layout that read the file cleanly. Parsing never saves it: the import
    # does, in the same transaction as the rows (M11), so a failed import remembers nothing.
    learned_layout: CsvLayout | None = None
    # What the statement was read with: kept in the draft, so the fix-up screen's "Check
    # again" runs the same `verify` as this step (R-M3-23 (c)).
    basis: Basis | None = None


_FIELDS = ("raw_description", "merchant", "bank_category", "bank_type")


def restore_masked(parsed: ParsedStatement, doc: Document) -> None:
    """Put the account details masked out of a row's line (`Document.masked`) back into the
    text the reader copied from it, so a description is stored as printed. Only on this
    device: the details were never sent."""
    for row in parsed.rows:
        masked = doc.masked.get(row.ref.split("#", 1)[0])
        if masked is None:
            continue
        for name in _FIELDS:
            value = getattr(row, name)
            if value:
                for placeholder, original in masked.hidden.items():
                    value = value.replace(placeholder, original)
                setattr(row, name, value)


def level_for(doc: Document) -> CheckLevel:
    return "screenshot" if doc.kind == "image" else "full"


def parse_document(
    doc: Document,
    path: Path,
    evidence: Evidence,
    account_kind: AccountKind,
    *,
    registry: LayoutRegistry,
    llm: StructuredLLM,
    run: Any,
    context_window: int | None,
    today: dt.date,
    limits: ReaderLimits,
    prompts_dir: Path | None = None,
    names: Sequence[str] = (),
    extract_limits: ExtractLimits | None = None,
    local_seconds: float = LOCAL_SECONDS,
) -> ParseOutcome:
    """`context_window` is the read model's, or None when no model is set up (an AI step then
    raises NoModelConfigured through `llm`). `names` are the household's own names, hidden
    from layout learning like any account detail. `extract_limits` bound the sandbox that
    reads a CAMT.053 file's XML. Each pass of this device's own over the file (reading it,
    checking the rows) stops after `local_seconds` (R-M3-23 (d)); AI calls have their own
    budget."""
    outcome = _parse(
        doc,
        path,
        evidence,
        account_kind,
        registry=registry,
        llm=llm,
        run=run,
        context_window=context_window,
        today=today,
        limits=limits,
        prompts_dir=prompts_dir,
        names=names,
        extract_limits=extract_limits,
        local_seconds=local_seconds,
    )
    if outcome.basis is None:  # a file refused before any row was read
        outcome.basis = Basis(account_kind=account_kind, evidence=evidence)
        if doubt := kind_doubt(evidence, account_kind):
            outcome.errors.append(doubt)
    return outcome


def _parse(
    doc: Document,
    path: Path,
    evidence: Evidence,
    account_kind: AccountKind,
    *,
    registry: LayoutRegistry,
    llm: StructuredLLM,
    run: Any,
    context_window: int | None,
    today: dt.date,
    limits: ReaderLimits,
    prompts_dir: Path | None,
    names: Sequence[str],
    extract_limits: ExtractLimits | None,
    local_seconds: float,
) -> ParseOutcome:
    level = level_for(doc)
    deadline = after(local_seconds)
    basis = Basis(account_kind=account_kind, evidence=evidence)
    if doc.kind in ("ofx", "qif", "camt053"):
        try:
            if doc.kind == "camt053":  # the XML is parsed in the sandbox, never in the app
                facts = read_camt(path, extract_limits or ExtractLimits())
                parsed = parsed_from_facts(facts, deadline=deadline)
            else:
                text = decode_text(path.read_bytes())
                read = parse_ofx if doc.kind == "ofx" else parse_qif
                parsed = read(text, deadline=deadline)
        except (OfxError, QifError, CamtError) as exc:
            return ParseOutcome(
                parsed=ParsedStatement(importer=doc.kind),
                errors=[safe_error_text(exc)],
                info={"importer": doc.kind},
            )
        checked = verify.verify(doc, parsed, basis, level=level, deadline=deadline)
        return ParseOutcome(
            parsed=parsed, errors=checked.errors, info={"importer": parsed.importer}, basis=basis
        )
    if doc.kind in ("csv", "xlsx"):
        # A learned layout only for this side of account (a card's or a bank account's).
        layout = registry.match(doc, kind=account_kind)
        if layout is not None:
            try:
                result = parse_with_layout(doc, layout, deadline=deadline)
            except LayoutMismatch as exc:
                return ParseOutcome(
                    parsed=ParsedStatement(importer=f"csv:{layout.id}"),
                    errors=[safe_error_text(exc)],
                    info={"importer": f"csv:{layout.id}"},
                )
            if layout.source == "learned":  # a bank's own layout is no one's guess
                basis = basis.model_copy(update={"layout": layout})
            checked = verify.verify(doc, result.parsed, basis, level=level, deadline=deadline)
            return ParseOutcome(
                parsed=result.parsed,
                errors=result.problems + checked.errors,
                info={"importer": result.parsed.importer},
                basis=basis,
            )
        outcome = propose_layout(
            doc,
            llm=llm,
            run=run,
            account_kind=account_kind,
            names=names,
            max_attempts=limits.max_attempts_per_chunk,
            prompt=load_prompt("csv_mapping", prompts_dir),
            local_seconds=local_seconds,
        )
        if outcome.layout is not None and outcome.result is not None:
            # The rows a fresh mapping read go through the same checks as any importer's.
            basis = basis.model_copy(update={"layout": outcome.layout, "layout_new": True})
            parsed = outcome.result.parsed
            checked = verify.verify(doc, parsed, basis, level=level, deadline=after(local_seconds))
            errors = [*outcome.result.problems, *checked.errors]
            if errors:  # read the file, but don't trust the layout enough to keep it yet
                return ParseOutcome(
                    parsed=parsed,
                    errors=errors,
                    info={"importer": "csv:unknown", "attempts": outcome.attempts},
                    pending_layout=outcome.layout,
                    basis=basis,
                )
            parsed = parsed.model_copy(update={"importer": f"csv:{outcome.layout.id}"})
            return ParseOutcome(
                parsed=parsed,
                info={"importer": parsed.importer, "attempts": outcome.attempts},
                learned_layout=outcome.layout,
                basis=basis,
            )
        parsed = (
            outcome.result.parsed if outcome.result else ParsedStatement(importer="csv:unknown")
        )
        return ParseOutcome(
            parsed=parsed,
            errors=outcome.errors,
            info={"importer": "csv:unknown", "attempts": outcome.attempts},
        )
    # PDF, plain text or a screenshot: the AI transcribes, Check verifies.
    perspective = "card" if account_kind == "credit_card" else "household"
    read = read_document(
        doc,
        llm=llm,
        run=run,
        perspective=perspective,
        level=level,
        account=ACCOUNT_LABELS.get(account_kind, "bank account"),
        facts=evidence.facts,
        today=today,
        context_window=context_window or 128_000,
        rows_per_chunk=limits.rows_per_chunk,
        max_attempts=limits.max_attempts_per_chunk,
        parallel=limits.parallel_chunks,
        prompt=load_prompt("read", prompts_dir),
        deadline=deadline,
    )
    # The period comes from the header, read on this device; balances and signs too, in
    # `verify` (the model's opening balance is never used, its closing one only when it is
    # the running balance printed on the last row).
    parsed, facts = read.parsed, evidence.facts
    restore_masked(parsed, doc)
    if facts.period_start and facts.period_end:
        parsed.period_start, parsed.period_end = facts.period_start, facts.period_end
    checked = verify.verify(doc, parsed, basis, level=level, deadline=after(local_seconds))
    errors = [*read.errors, *checked.errors]
    if not parsed.rows:
        errors.append("No transactions were read from this file, so it needs a look.")
    elif not read.ok and not errors:
        errors.append("This statement couldn't be read reliably, so it needs a look.")
    return ParseOutcome(
        parsed=parsed,
        # The checks quote the model's text: clean and cap it like every error.
        errors=list(dict.fromkeys(tidy(e) for e in errors)),
        level=level,
        info={
            "importer": "ai-read",
            "attempts": read.attempts,
            "chunks": read.chunks,
            "signs_repaired": checked.repaired,
        },
        basis=basis,
    )
