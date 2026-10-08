"""Fingerprints and duplicate detection (spec §6.2 step 5)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import date, timedelta
from typing import NamedTuple

from pydantic import BaseModel, Field

from tuppence.ingest.models import ParsedRow
from tuppence.ingest.textnum import pounds

SOFT_DAYS = 2
_TOKEN = re.compile(r"[a-z0-9]+")


def normalise_description(raw: str) -> str:
    return " ".join(raw.casefold().split())


def fingerprint(
    account_id: str, day: date, amount_pence: int, raw_description: str, occurrence: int
) -> str:
    """24 hex chars. The same account, date, amount and description always hash together;
    `occurrence` tells identical lines on the same statement apart."""
    parts = [
        account_id,
        day.isoformat(),
        pounds(amount_pence),
        normalise_description(raw_description),
    ]
    text = "|".join([*parts, str(occurrence)])
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def assign_fingerprints(rows: Sequence[ParsedRow], account_id: str) -> list[tuple[str, int]]:
    """(fingerprint, occurrence) for each row, in order."""
    seen: dict[tuple[date, int, str], int] = {}
    out: list[tuple[str, int]] = []
    for row in rows:
        key = (row.date, row.amount_pence, normalise_description(row.raw_description))
        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1
        out.append(
            (
                fingerprint(
                    account_id, row.date, row.amount_pence, row.raw_description, occurrence
                ),
                occurrence,
            )
        )
    return out


def description_tokens(raw: str) -> frozenset[str]:
    return frozenset(t for t in _TOKEN.findall(raw.casefold()) if len(t) >= 3 and not t.isdigit())


def similar_descriptions(a: str, b: str) -> bool:
    ta, tb = description_tokens(a), description_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta or len(ta & tb) / len(ta | tb) >= 0.5


class Existing(NamedTuple):
    id: str
    date: date
    amount_pence: int
    raw_description: str
    fingerprint: str


class DedupePlan(BaseModel):
    insert: list[int] = Field(default_factory=list)  # indexes into the new rows
    exact: list[int] = Field(default_factory=list)  # same fingerprint already stored
    similar: dict[int, str] = Field(default_factory=dict)  # new row index → existing transaction id


def plan_dedupe(
    rows: Sequence[ParsedRow],
    fingerprints: Sequence[str],
    existing: Sequence[Existing],
    *,
    window: tuple[date, date],
    exact_only: Sequence[Existing] = (),
) -> DedupePlan:
    """Decide which new rows to store.

    `existing` holds this account's stored rows from *other* statements. A row whose
    fingerprint is stored is an exact duplicate. Otherwise a stored row inside the
    overlap `window` with the same amount, a date at most 2 days away and a similar
    description is the same transaction seen in another format (a CSV and a PDF of
    the same month, or a screenshot). Each stored row matches at most one new row,
    same-day matches first. `exact_only` rows (a statement's own rows from its earlier read)
    match only by fingerprint: a corrected row replaces its old self.
    """
    plan = DedupePlan()
    stored = {e.fingerprint: e for e in [*existing, *exact_only]}
    used: set[str] = set()
    pending: list[int] = []
    for i, fp in enumerate(fingerprints):
        if fp in stored:
            plan.exact.append(i)
            used.add(stored[fp].id)
        else:
            pending.append(i)
    lo, hi = window
    # Stored rows inside the window by amount, then by day: each new row looks only at rows of
    # its own amount a few days either side (M10: this runs inside the import's write lock).
    by_amount: dict[int, dict[date, list[tuple[int, Existing]]]] = {}
    for order, e in enumerate(existing):
        if lo <= e.date <= hi:
            by_amount.setdefault(e.amount_pence, {}).setdefault(e.date, []).append((order, e))
    for max_gap in (0, SOFT_DAYS):
        for i in list(pending):
            row = rows[i]
            if not lo <= row.date <= hi:
                continue
            days = by_amount.get(row.amount_pence)
            if not days:
                continue
            near = [
                (abs(gap), order, e)
                for gap in range(-max_gap, max_gap + 1)
                for order, e in days.get(row.date + timedelta(days=gap), ())
            ]
            for _, _, e in sorted(near, key=lambda item: (item[0], item[1])):
                if e.id in used:
                    continue
                if similar_descriptions(e.raw_description, row.raw_description):
                    plan.similar[i] = e.id
                    used.add(e.id)
                    pending.remove(i)
                    break
    plan.insert = pending
    return plan
