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
from tuppence.ingest.check import check_document, check_statement
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
from tuppence.ingest.registry import LayoutRegistry
from tuppence.ingest.textnum import decode_text

ACCOUNT_LABELS: dict[str, str] = {
    "current": "current account",
    "savings": "savings account",
    "credit_card": "credit card (the statement prints purchases as positive figures)",
}


class ReaderLimits(BaseModel):
    max_attempts_per_chunk: int = 3
    rows_per_chunk: int = 40
    parallel_chunks: int = 2


class ParseOutcome(BaseModel):
    parsed: ParsedStatement
    errors: list[str] = Field(default_factory=list)
    level: CheckLevel = "full"
    info: dict[str, Any] = Field(default_factory=dict)


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
    # Period and balances read on this device from the withheld header win; the model's
    # own values are only fallbacks when the header had none.
    parsed, facts = read.parsed, evidence.facts
    if facts.period_start and facts.period_end:  # read locally from the header: wins over the AI
        parsed.period_start, parsed.period_end = facts.period_start, facts.period_end
    if facts.opening_pence is not None:
        parsed.opening_balance_pence = facts.opening_pence
    if facts.closing_pence is not None:
        parsed.closing_balance_pence = facts.closing_pence
    errors = list(dict.fromkeys(read.errors + check_statement(parsed, level=level, dates=True)))
    return ParseOutcome(
        parsed=parsed,
        errors=errors,
        level=level,
        info={"importer": "ai-read", "attempts": read.attempts, "chunks": read.chunks},
    )
