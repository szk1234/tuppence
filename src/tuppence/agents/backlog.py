"""The Backlog sweep (spec §8.2 "Doubt"): code only.

Each run queues understanding rows for the next run's Categoriser, at most `cap`, biggest
amounts first, in two tiers:

1. Rows the Categoriser will act on: not sorted yet, filed in a category that has since been
   retired, decided before a later change to their merchant or category (stale), or an AI
   guess that hasn't had its second look.
2. Other rows Tuppence isn't sure about (a guess, or below `revisit_below`) that haven't been
   looked at under the current knowledge version: something has changed since (a new rule,
   say) that may file them.

Rows already looked at under the current knowledge version are left out: the Categoriser
would do nothing new with them, and queuing them again would fill the cap with the same rows
every run while the rest wait. The Categoriser stamps a row it looks at and keeps with the
version it looked under.
"""

from __future__ import annotations

import sqlite3
from collections import Counter

from tuppence.knowledge.versions import STALE_SQL, KnowledgeVersions

ACT = ("unknown", "retired", "stale", "unreviewed")  # tier 1: the Categoriser acts on these
REASON_SQL = (
    "CASE WHEN u.status = 'unknown' THEN 'unknown'"  # noqa: S608 - constant SQL
    " WHEN u.category_id IN (SELECT id FROM category WHERE retired = 1) THEN 'retired'"
    f" WHEN {STALE_SQL} THEN 'stale'"
    " WHEN u.decided_by = 'llm' AND u.status = 'guessed' THEN 'unreviewed'"
    " WHEN u.status = 'guessed' THEN 'guessed'"
    " WHEN u.confidence < :below THEN 'low_confidence' END"
)
_ACT_LIST = ", ".join(f"'{r}'" for r in ACT)


def sweep(conn: sqlite3.Connection, *, revisit_below: float, cap: int) -> dict[str, int]:
    """Mark up to `cap` rows `queued`, inside the caller's transaction. Returns counts by reason."""
    rows = conn.execute(
        "SELECT transaction_id, reason FROM ("  # noqa: S608 - constant SQL
        f" SELECT u.transaction_id, u.knowledge_version, {REASON_SQL} AS reason,"
        " ABS(t.amount_pence) AS size, t.date"
        ' FROM understanding u JOIN "transaction" t ON t.id = u.transaction_id'
        " WHERE u.status != 'confirmed' AND u.ignored = 0 AND u.waiting IS NULL)"
        f" WHERE reason IN ({_ACT_LIST})"
        " OR (reason IS NOT NULL AND knowledge_version < :current)"
        f" ORDER BY reason NOT IN ({_ACT_LIST}), size DESC, date DESC, transaction_id"
        " LIMIT :cap",
        {"below": revisit_below, "cap": cap, "current": KnowledgeVersions.current_in(conn)},
    ).fetchall()
    conn.executemany(
        "UPDATE understanding SET waiting = 'queued' WHERE transaction_id = ?",
        [(r["transaction_id"],) for r in rows],
    )
    return dict(Counter(r["reason"] for r in rows))


def queued(conn: sqlite3.Connection, *, limit: int) -> list[str]:
    """Rows waiting for the Categoriser (queued, deferred or awaiting AI), biggest first."""
    return [
        r[0]
        for r in conn.execute(
            'SELECT u.transaction_id FROM understanding u JOIN "transaction" t'
            " ON t.id = u.transaction_id WHERE u.waiting IS NOT NULL AND u.status != 'confirmed'"
            " ORDER BY ABS(t.amount_pence) DESC, t.date DESC, t.id LIMIT ?",
            [limit],
        )
    ]
