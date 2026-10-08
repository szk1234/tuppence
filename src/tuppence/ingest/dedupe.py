"""Fingerprints and duplicate detection (spec §6.2 step 5)."""

from __future__ import annotations

import hashlib
import heapq
import re
from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from typing import NamedTuple

from pydantic import BaseModel, Field

from tuppence.ingest.clock import Deadline, ticking
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
    return _similar(description_tokens(a), description_tokens(b))


def _similar(ta: frozenset[str], tb: frozenset[str]) -> bool:
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


# Stored rows looked at for one new row in one pass, beyond those already taken. A real
# statement has a handful of the same amount within a few days; only a crafted file has more,
# and it can't make planning slow (R-M3-23 (d)).
MAX_CANDIDATES = 32


class _Bucket:
    """Stored rows of one amount on one day, in order, skipping those already matched in
    near-constant time (each taken row points past itself)."""

    def __init__(self) -> None:
        self.rows: list[tuple[int, Existing]] = []
        self._next: list[int] = []

    def add(self, order: int, row: Existing) -> None:
        self._next.append(len(self.rows))
        self.rows.append((order, row))

    def take(self, k: int) -> None:
        self._next[k] = k + 1

    def unused(self, k: int) -> int:
        root = k
        while root < len(self._next) and self._next[root] != root:
            root = self._next[root]
        while k < len(self._next) and self._next[k] != k:
            self._next[k], k = root, self._next[k]
        return root

    def walk(self) -> Iterator[int]:
        k = self.unused(0)
        while k < len(self.rows):
            yield k
            k = self.unused(k + 1)


def _nearest(buckets: Sequence[_Bucket]) -> Iterator[tuple[_Bucket, int]]:
    """Untaken rows of buckets the same distance away (the day before and the day after), in
    stored order."""

    def walk(i: int, bucket: _Bucket) -> Iterator[tuple[int, int, int]]:
        for k in bucket.walk():
            yield bucket.rows[k][0], k, i

    for _, k, i in heapq.merge(*(walk(i, bucket) for i, bucket in enumerate(buckets))):
        yield buckets[i], k


def plan_dedupe(
    rows: Sequence[ParsedRow],
    fingerprints: Sequence[str],
    existing: Sequence[Existing],
    *,
    window: tuple[date, date],
    exact_only: Sequence[Existing] = (),
    deadline: Deadline | None = None,
) -> DedupePlan:
    """Decide which new rows to store.

    `existing` holds this account's stored rows from *other* statements. A row whose
    fingerprint is stored is an exact duplicate. Otherwise a stored row inside the
    overlap `window` with the same amount, a date at most 2 days away and a similar
    description is the same transaction seen in another format (a CSV and a PDF of
    the same month, or a screenshot). Each stored row matches at most one new row,
    same-day matches first. `exact_only` rows (a statement's own rows from its earlier read)
    match only by fingerprint: a corrected row replaces its old self.

    Time is in proportion to the rows: each new row looks at no more than `MAX_CANDIDATES`
    untaken stored rows a pass.
    """
    tick = ticking(deadline)
    plan = DedupePlan()
    stored = {e.fingerprint: e for e in [*existing, *exact_only]}
    used: set[str] = set()
    pending: list[int] = []
    for i, fp in enumerate(fingerprints):
        tick(i)
        if fp in stored:
            plan.exact.append(i)
            used.add(stored[fp].id)
        else:
            pending.append(i)
    lo, hi = window
    # Stored rows inside the window by amount, then by day: each new row looks only at rows of
    # its own amount a few days either side (M10: this runs inside the import's write lock).
    by_amount: dict[int, dict[date, _Bucket]] = {}
    for order, e in enumerate(existing):
        tick(order)
        if lo <= e.date <= hi:
            by_amount.setdefault(e.amount_pence, {}).setdefault(e.date, _Bucket()).add(order, e)
    tokens: dict[str, frozenset[str]] = {}

    def tokens_of(text: str) -> frozenset[str]:
        if text not in tokens:
            tokens[text] = description_tokens(text)
        return tokens[text]

    for max_gap in (0, SOFT_DAYS):
        matched: set[int] = set()
        for n, i in enumerate(pending):
            tick(n)
            row = rows[i]
            if not lo <= row.date <= hi:
                continue
            days = by_amount.get(row.amount_pence)
            if not days:
                continue
            mine = tokens_of(row.raw_description)
            looked = 0
            for gap in range(max_gap + 1):
                near = [
                    bucket
                    for day in {row.date - timedelta(days=gap), row.date + timedelta(days=gap)}
                    if (bucket := days.get(day)) is not None
                ]
                for bucket, k in _nearest(near):
                    e = bucket.rows[k][1]
                    if e.id in used:  # matched by fingerprint, or taken by another row
                        bucket.take(k)
                        continue
                    if looked >= MAX_CANDIDATES:
                        break
                    looked += 1
                    if _similar(tokens_of(e.raw_description), mine):
                        plan.similar[i] = e.id
                        used.add(e.id)
                        bucket.take(k)
                        matched.add(i)
                        break
                if i in matched or looked >= MAX_CANDIDATES:
                    break
        pending = [i for i in pending if i not in matched]
    plan.insert = pending
    return plan
