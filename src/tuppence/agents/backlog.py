"""The Backlog sweep (spec §8.2 "Doubt"): code only.

Each run re-queues understanding rows Tuppence isn't sure about: unknown or guessed, below
the confidence threshold, or decided before a later change to their merchant or category
(their knowledge version is stale). Biggest amounts first, capped. The next run's
Categoriser takes them; rows the model already decided under the current knowledge are
not sent to it again.
"""

from __future__ import annotations

import sqlite3
from collections import Counter

from tuppence.knowledge.versions import STALE_SQL

REASON_SQL = (
    "CASE WHEN u.status = 'unknown' THEN 'unknown' WHEN u.status = 'guessed' THEN 'guessed'"
    " WHEN u.confidence < :below THEN 'low_confidence' ELSE 'stale' END"
)


def sweep(conn: sqlite3.Connection, *, revisit_below: float, cap: int) -> dict[str, int]:
    """Mark up to `cap` rows `queued`, inside the caller's transaction. Returns counts by reason."""
    rows = conn.execute(
        f"SELECT u.transaction_id, {REASON_SQL} AS reason"  # noqa: S608 - constant SQL
        ' FROM understanding u JOIN "transaction" t ON t.id = u.transaction_id'
        " WHERE u.status != 'confirmed' AND u.ignored = 0 AND u.waiting IS NULL"
        f" AND (u.status IN ('unknown', 'guessed') OR u.confidence < :below OR {STALE_SQL})"
        " ORDER BY ABS(t.amount_pence) DESC, t.date DESC, t.id LIMIT :cap",
        {"below": revisit_below, "cap": cap},
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
