"""Run one corpus case through extract → identify → parse → check, with no database."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from evals.corpus import Case, Row
from tuppence.core.errors import safe_error_text
from tuppence.ingest.check import balance_verified
from tuppence.ingest.dedupe import similar_descriptions
from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.identify import identify
from tuppence.ingest.mapping import summarise_checks
from tuppence.ingest.models import ParsedRow
from tuppence.ingest.parse import ReaderLimits, parse_document
from tuppence.ingest.registry import LayoutRegistry, load_bank_pack
from tuppence.llm.budget import RunBudget

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "statements"
TODAY = dt.date(2026, 11, 1)
EVAL_KEY = b"tuppence-eval-fingerprint-key!!"  # fingerprints only; never a real key


class CaseResult(BaseModel):
    id: str
    ok: bool
    importer: str = ""
    expected_rows: int = 0
    rows: int = 0
    matched: int = 0
    accuracy: float = 0.0
    balance_verified: bool = False
    llm_calls: int = 0
    tokens: int = 0
    cost_gbp: float = 0.0
    seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)


DEFAULT_MAX_GBP = 2.0  # what a whole eval run may spend on a paid model, unless told otherwise


def eval_budget(max_gbp: float = DEFAULT_MAX_GBP) -> RunBudget:
    """Counts usage for the report. Calls, tokens and time are generous; money is capped (the
    app's own monthly cap still applies on top)."""
    return RunBudget(max_calls=10_000, max_tokens=100_000_000, max_gbp=max_gbp, max_seconds=3_600)


def plain_errors(errors: list[str]) -> list[str]:
    """Check failures as fixed descriptions with counts: what the model read (a figure, a
    payee, a line ref) never goes into a saved result."""
    return summarise_checks(errors)


def score(expected: list[Row], rows: list[ParsedRow]) -> tuple[int, float]:
    remaining = list(rows)
    matched = 0
    for day, pence, description in expected:
        for i, row in enumerate(remaining):
            if (
                row.date == day
                and row.amount_pence == pence
                and similar_descriptions(description, row.raw_description)
            ):
                matched += 1
                remaining.pop(i)
                break
    total = max(len(expected), len(rows))
    return matched, (matched / total) if total else 1.0


def run_case(
    case: Case,
    *,
    llm: Any,
    context_window: int | None,
    today: dt.date = TODAY,
    fixtures: Path = FIXTURES,
    budget: RunBudget | None = None,
) -> CaseResult:
    pack = load_bank_pack()
    registry = LayoutRegistry(pack)
    path = fixtures / case.path
    budget = budget or eval_budget()
    started = time.monotonic()
    try:
        doc = extract_document(
            path,
            case.kind,
            sha256="eval",
            limits=ExtractLimits(),
            known_header=registry.is_known_header,
        )
        evidence = identify(doc, pack=pack, registry=registry, key=EVAL_KEY)
        outcome = parse_document(
            doc,
            path,
            evidence,
            case.account_kind,
            registry=registry,
            llm=llm,
            run=budget,
            context_window=context_window,
            today=today,
            limits=ReaderLimits(),
        )
    except Exception as exc:  # noqa: BLE001 - a failing case is a result, not a crash
        return CaseResult(
            id=case.id,
            ok=False,
            errors=[safe_error_text(exc)],
            seconds=round(time.monotonic() - started, 2),
            expected_rows=len(case.expected),
        )
    matched, accuracy = score(case.expected, outcome.parsed.rows)
    verified = balance_verified(outcome.parsed, outcome.errors, outcome.level)
    importer = outcome.info.get("importer", "")
    ok = (
        not outcome.errors
        and accuracy == 1.0
        and verified == case.balance_verified
        and (not case.importer or importer == case.importer)
    )
    return CaseResult(
        id=case.id,
        ok=ok,
        importer=importer,
        expected_rows=len(case.expected),
        rows=len(outcome.parsed.rows),
        matched=matched,
        accuracy=round(accuracy, 4),
        balance_verified=verified,
        llm_calls=budget.calls,
        tokens=budget.tokens,
        cost_gbp=round(budget.gbp, 4),
        seconds=round(time.monotonic() - started, 2),
        errors=plain_errors(outcome.errors)[:10],
    )
