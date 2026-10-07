"""Step 3 of the pipeline: from a Document to checked rows (spec §6.2 steps 3–4).

Fixed importers first; the AI only for an unfamiliar CSV layout (once) and for
PDF, OCR and plain-text statements. Used by the ingest graph and the eval harness.
"""

from __future__ import annotations

import datetime as dt
import re
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
from tuppence.ingest.textprep import has_amount

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
    # A newly proposed CSV layout that read the file but whose signs can't be confirmed yet.
    # It is not saved; the caller may save it once the person has confirmed the statement.
    pending_layout: CsvLayout | None = None


_CARD_CREDIT = re.compile(
    r"payment\s*(?:-\s*)?(?:received|thank)|thank\s*you|\brefund|\bcash\s*back\b"
    r"|direct\s+debit\s+payment",
    re.IGNORECASE,
)
_TYPE_CREDIT = {"cr", "credit", "payment", "refund"}
_TYPE_DEBIT = {"dr", "debit", "purchase", "sale"}
_ACCOUNT_WORDS = {"current": "a current account", "savings": "a savings account"}


def _card_evidence(parsed: ParsedStatement) -> tuple[int, int]:
    """(rows that agree, rows that disagree) with the signs read: a payment to the card or a
    refund is money in, a row typed as a purchase or debit is money out."""
    agree = disagree = 0
    for row in parsed.rows:
        kind = (row.bank_type or "").strip().casefold()
        if kind in _TYPE_CREDIT or _CARD_CREDIT.search(row.raw_description):
            want = 1
        elif kind in _TYPE_DEBIT:
            want = -1
        else:
            continue
        if row.amount_pence * want > 0:
            agree += 1
        elif row.amount_pence:
            disagree += 1
    return agree, disagree


def sign_doubt(
    parsed: ParsedStatement, account_kind: AccountKind, layout: CsvLayout, *, new: bool
) -> str | None:
    """Why a learned layout's signs can't be trusted yet, or None.

    Two money columns are checked by Check against their headings. A single amount column
    adds up (with a balance column too) whichever way round it is read, so:
    - on a current or savings account, a layout that reads it the card's way is a
      contradiction;
    - on a card, the rows are the evidence: a payment or refund read as money out (or a
      purchase read as money in) means back to front, and a new layout with no such row
      waits for the person to confirm it."""
    if layout.amount is None:
        return None
    if account_kind in _ACCOUNT_WORDS:
        if layout.perspective != "card":
            return None
        return (
            "The remembered layout for this file reads purchases as positive, as a card "
            f"statement does, which doesn't fit {_ACCOUNT_WORDS[account_kind]}. The signs may "
            "be back to front, so please check them."
        )
    agree, disagree = _card_evidence(parsed)
    if disagree:
        return (
            f"{disagree} of these rows (payments to the card, refunds or purchases) would be "
            "stored the wrong way round, so the signs may be back to front. Please check them."
        )
    if not agree and new:
        return (
            "Nothing in this file shows which way round the card's amounts are (such as a "
            "payment to the card or a refund), so please check the signs before this layout "
            "is remembered."
        )
    return None


def _held_back_amounts(doc: Document) -> str | None:
    """A screenshot's withheld lines that show an amount may be transactions: say so (without
    quoting them) instead of losing them silently."""
    by_ref = doc.by_ref()
    count = sum(1 for ref in doc.preamble_refs if ref in by_ref and has_amount(by_ref[ref].text))
    if count == 0:
        return None
    if count == 1:
        return (
            "A line of this screenshot with an amount on it was held back from the AI because "
            "it also shows account details. It may be a transaction, so please check it."
        )
    return (
        f"{count} lines of this screenshot with an amount on them were held back from the AI "
        "because they also show account details. They may be transactions, so please check them."
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
            if layout.source == "learned" and (
                doubt := sign_doubt(result.parsed, account_kind, layout, new=False)
            ):
                errors.append(doubt)
            return ParseOutcome(
                parsed=result.parsed, errors=errors, info={"importer": result.parsed.importer}
            )
        outcome = propose_layout(
            doc,
            llm=llm,
            run=run,
            account_kind=account_kind,
            max_attempts=limits.max_attempts_per_chunk,
            prompt=load_prompt("csv_mapping", prompts_dir),
        )
        if outcome.layout is not None and outcome.result is not None:
            doubt = sign_doubt(outcome.result.parsed, account_kind, outcome.layout, new=True)
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
    # Directions are proved from balances read on this device only, never the model's.
    repair = repair_signs(doc, parsed, opening=local.opening, closing=local.closing, level=level)
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
    if level == "screenshot" and (held := _held_back_amounts(doc)):
        errors.append(held)
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
