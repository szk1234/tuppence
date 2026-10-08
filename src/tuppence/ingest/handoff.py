"""Hand a newly imported statement to the analysis workflow (spec §6.2 step 6, §8.3).

M3 has no analysis yet. The `analysis` job is queued exactly as M4 will consume it (one
pending job per household, 30 s debounce, statement ids merged by the queue's merge registry:
`JobQueue(db, merges={ANALYSIS_JOB: merge_statement_ids})`). M3 registers no handler for it,
so the worker never claims it and the jobs wait for M4.
"""

from __future__ import annotations

from typing import Any

ANALYSIS_JOB = "analysis"
ANALYSIS_SCOPE = "household"
DEBOUNCE_S = 30.0


def merge_statement_ids(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    return {"statement_ids": sorted({*old.get("statement_ids", []), *new.get("statement_ids", [])})}


def enqueue_analysis(queue: Any, statement_id: str) -> int:
    return queue.enqueue(
        ANALYSIS_JOB,
        scope_key=ANALYSIS_SCOPE,
        payload={"statement_ids": [statement_id]},
        debounce_s=DEBOUNCE_S,
    )
