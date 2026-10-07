"""Step 3 of the pipeline: from a Document to checked rows (spec §6.2 steps 3–4).

Fixed importers first; the AI only for an unfamiliar CSV layout (once) and for
PDF, OCR and plain-text statements. Used by the ingest graph and the eval harness.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tuppence.core.errors import safe_error_text
from tuppence.ingest.balances import local_balances, repair_signs
from tuppence.ingest.check import check_document, check_rows, check_statement
from tuppence.ingest.extract import ExtractLimits, read_camt
from tuppence.ingest.identify import Evidence
from tuppence.ingest.importers.camt import CamtError, parsed_from_facts
from tuppence.ingest.importers.csv_layout import LayoutMismatch, parse_with_layout
from tuppence.ingest.importers.ofx import OfxError, parse_ofx
from tuppence.ingest.importers.qif import QifError, parse_qif
from tuppence.ingest.mapping import propose_layout
from tuppence.ingest.models import AccountKind, CheckLevel, Document, ParsedStatement
from tuppence.ingest.prompts import load_prompt
from tuppence.ingest.reader import StructuredLLM, read_document
from tuppence.ingest.registry import CsvLayout, LayoutRegistry
from tuppence.ingest.textnum import decode_text

ACCOUNT_LABELS: dict[str, str] = {
    "current": "current account",
    "savings": "savings account",
    "credit_card": "credit card (the statement prints purchases as positive figures)",
}


class ReaderLimits(BaseModel):
    max_attempts_per_chunk: int = Field(default=3, ge=1, le=3)
    rows_per_chunk: int = Field(default=40, ge=1, le=200)
    parallel_chunks: int = Field(default=2, ge=1, le=8)


class ParseOutcome(BaseModel):
    parsed: ParsedStatement
    errors: list[str] = Field(default_factory=list)
    level: CheckLevel = "full"
    info: dict[str, Any] = Field(default_factory=dict)
    # A newly proposed CSV layout that read the file but whose signs look wrong. It is not
    # saved; the caller may save it once the person has confirmed the statement.
    pending_layout: CsvLayout | None = None


def sign_doubt(parsed: ParsedStatement, account_kind: AccountKind) -> str | None:
    """Unverifiable sign conventions: a single amount column can add up (with a balance
    column too) whichever way round it was read, so the account type is the evidence. On a
    current account or a card most amounts are money out; a file where most are money in has
    probably been read back to front."""
    if account_kind not in ("current", "credit_card"):
        return None
    rows = [r for r in parsed.rows if r.amount_pence != 0]
    if len(rows) < 4:
        return None
    incoming = sum(1 for r in rows if r.amount_pence > 0)
    if incoming / len(rows) <= 0.6:
        return None
    what = "card" if account_kind == "credit_card" else "current account"
    return (
        f"{incoming} of these {len(rows)} amounts would be money in, which is unusual for a "
        f"{what}. The signs may be back to front, so please check them."
    )


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
) -> ParseOutcome:
    """`context_window` is the read model's, or None when no model is set up (an AI step then
    raises NoModelConfigured through `llm`)."""
    level = level_for(doc)
    if doc.kind in ("ofx", "qif", "camt053"):
        try:
            if doc.kind == "camt053":  # the XML is parsed in the sandbox, never in the app
                parsed = parsed_from_facts(read_camt(path, ExtractLimits()))
            else:
                text = decode_text(path.read_bytes())
                parsed = parse_ofx(text) if doc.kind == "ofx" else parse_qif(text)
        except (OfxError, QifError, CamtError) as exc:
            return ParseOutcome(
                parsed=ParsedStatement(importer=doc.kind),
                errors=[safe_error_text(exc)],
                info={"importer": doc.kind},
            )
        return ParseOutcome(
            parsed=parsed, errors=check_document(doc, parsed), info={"importer": parsed.importer}
        )
    if doc.kind in ("csv", "xlsx"):
        layout = registry.match(doc)
        if layout is not None:
            try:
                result = parse_with_layout(doc, layout)
            except LayoutMismatch as exc:
                return ParseOutcome(
                    parsed=ParsedStatement(importer=f"csv:{layout.id}"),
                    errors=[safe_error_text(exc)],
                    info={"importer": f"csv:{layout.id}"},
                )
            errors = result.problems + check_document(doc, result.parsed)
            if layout.source == "learned" and (doubt := sign_doubt(result.parsed, account_kind)):
                errors.append(doubt)
            return ParseOutcome(
                parsed=result.parsed, errors=errors, info={"importer": result.parsed.importer}
            )
        outcome = propose_layout(
            doc,
            llm=llm,
            run=run,
            max_attempts=limits.max_attempts_per_chunk,
            prompt=load_prompt("csv_mapping", prompts_dir),
        )
        if outcome.layout is not None and outcome.result is not None:
            doubt = sign_doubt(outcome.result.parsed, account_kind)
            if doubt:  # read the file, but don't trust the layout enough to keep it
                parsed = outcome.result.parsed
                return ParseOutcome(
                    parsed=parsed,
                    errors=[doubt],
                    info={"importer": "csv:unknown", "attempts": outcome.attempts},
                    pending_layout=outcome.layout,
                )
            learned = registry.save_learned(doc, outcome.layout)
            parsed = outcome.result.parsed.model_copy(update={"importer": f"csv:{learned.id}"})
            return ParseOutcome(
                parsed=parsed, info={"importer": parsed.importer, "attempts": outcome.attempts}
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
    )
    # Period and balances come from the lines that were withheld from the model, read on this
    # device. The model's own values are only fallbacks when those lines gave none.
    parsed, facts = read.parsed, evidence.facts
    if facts.period_start and facts.period_end:
        parsed.period_start, parsed.period_end = facts.period_start, facts.period_end
    local = local_balances(doc, perspective=perspective)
    if local.opening is not None:
        parsed.opening_balance_pence = local.opening
    if local.closing is not None:
        parsed.closing_balance_pence = local.closing
    repair = repair_signs(doc, parsed, opening=parsed.opening_balance_pence, level=level)
    whole_file = check_rows(
        doc.lines,
        all_lines=doc.lines,
        context_refs=doc.header_refs,
        data_refs=doc.data_refs,
        parsed=parsed,
        level=level,
    )
    errors = [
        *read.errors,
        *whole_file,
        *check_statement(parsed, level=level, dates=True),
        *repair.errors,
    ]
    if not parsed.rows:
        errors.append("No transactions were read from this file, so it needs a look.")
    elif not read.ok and not errors:
        errors.append("This statement couldn't be read reliably, so it needs a look.")
    return ParseOutcome(
        parsed=parsed,
        errors=list(dict.fromkeys(errors)),
        level=level,
        info={
            "importer": "ai-read",
            "attempts": read.attempts,
            "chunks": read.chunks,
            "signs_repaired": repair.repaired,
        },
    )
