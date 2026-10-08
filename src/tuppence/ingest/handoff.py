"""Hand work to the analysis workflow (spec §6.2 step 6, §8.3).

One pending `analysis` job per household with a 30 s debounce: new statements, the
person's changes and the daily run all merge into it (spec §8.3: "Repeat triggers for the
same scope merge into one pending job"). The queue's merge registry does the merging:
`JobQueue(db, merges={ANALYSIS_JOB: merge_analysis_payload})`."""

from __future__ import annotations

from typing import Any

ANALYSIS_JOB = "analysis"
ANALYSIS_SCOPE = "household"
DEBOUNCE_S = 30.0


def merge_analysis_payload(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "statement_ids": sorted({*old.get("statement_ids", []), *new.get("statement_ids", [])})
    }
    reasons = sorted({*old.get("reasons", []), *new.get("reasons", [])})
    if reasons:
        merged["reasons"] = reasons
    return merged


merge_statement_ids = merge_analysis_payload  # M3's name, kept for its callers and tests


def enqueue_analysis(queue: Any, statement_id: str) -> int:
    return queue.enqueue(
        ANALYSIS_JOB,
        scope_key=ANALYSIS_SCOPE,
        payload={"statement_ids": [statement_id]},
        debounce_s=DEBOUNCE_S,
    )


def request_analysis(queue: Any, reason: str, *, debounce_s: float = DEBOUNCE_S) -> int:
    """Queue a run because something changed (a rule, a category, a correction, a statement
    removed or read again)."""
    return queue.enqueue(
        ANALYSIS_JOB,
        scope_key=ANALYSIS_SCOPE,
        payload={"reasons": [reason]},
        debounce_s=debounce_s,
    )
