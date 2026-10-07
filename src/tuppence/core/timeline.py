"""Effective-dated profile attributes (spec §5.3).

Intervals are [valid_from, valid_to): valid_to is exclusive and NULL means "still true".
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter, ValidationError

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.postcode import normalise_district
from tuppence.core.records import NotFound

SubjectType = Literal["household", "person", "account", "income_source"]

ALLOWED_ATTRIBUTES: dict[str, dict[str, TypeAdapter[Any]]] = {
    "person": {
        "employment_status": TypeAdapter(
            Literal["employed", "self_employed", "both", "retired", "student", "not_working"]
        ),
        "income_band": TypeAdapter(
            Literal["under_12570", "12570_50270", "50270_100000", "100000_125140", "over_125140"]
        ),
        "household_member": TypeAdapter(bool),
    },
    "household": {
        "nation": TypeAdapter(Literal["england", "wales", "scotland", "northern_ireland"]),
        "postcode_district": TypeAdapter(str),
    },
}


class TimelineEntry(BaseModel):
    id: int
    subject_type: str
    subject_id: str
    attribute: str
    value: Any
    valid_from: date
    valid_to: date | None
    source: str


def _validate(subject_type: str, attribute: str, value: Any) -> Any:
    adapters = ALLOWED_ATTRIBUTES.get(subject_type, {})
    if attribute not in adapters:
        raise InputError(f"'{attribute}' can't be recorded for a {subject_type}.")
    try:
        clean = adapters[attribute].validate_python(value)
    except ValidationError as exc:
        raise InputError(f"{attribute}: {exc.errors()[0]['msg']}") from exc
    if subject_type == "household" and attribute == "postcode_district":
        clean = normalise_district(clean)
    return clean


def _row(row: Any) -> TimelineEntry:
    return TimelineEntry(
        id=row["id"],
        subject_type=row["subject_type"],
        subject_id=row["subject_id"],
        attribute=row["attribute"],
        value=json.loads(row["value"]),
        valid_from=date.fromisoformat(row["valid_from"]),
        valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
        source=row["source"],
    )


class Timeline:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _require_subject(self, subject_type: str, subject_id: str) -> None:
        if subject_type == "household":
            if subject_id != "1":
                raise NotFound("household", subject_id)
        elif subject_type == "person":
            with self.db.connection() as conn:
                row = conn.execute("SELECT 1 FROM person WHERE id = ?", [subject_id]).fetchone()
            if row is None:
                raise NotFound("person", subject_id)

    def set(
        self,
        subject_type: str,
        subject_id: str,
        attribute: str,
        value: Any,
        valid_from: date,
        *,
        source: str = "user",
    ) -> TimelineEntry:
        clean = _validate(subject_type, attribute, value)
        self._require_subject(subject_type, subject_id)
        start = valid_from.isoformat()
        payload = json.dumps(clean)
        key = [subject_type, subject_id, attribute]
        with self.db.transaction() as conn:
            same = conn.execute(
                "SELECT id FROM profile_entry WHERE subject_type=? AND subject_id=? AND attribute=? AND valid_from=?",  # noqa: E501
                [*key, start],
            ).fetchone()
            if same is not None:
                conn.execute(
                    "UPDATE profile_entry SET value = ?, source = ? WHERE id = ?",
                    [payload, source, same["id"]],
                )
                entry_id = same["id"]
            else:
                nxt = conn.execute(
                    "SELECT valid_from FROM profile_entry WHERE subject_type=? AND subject_id=? AND attribute=?"  # noqa: E501
                    " AND valid_from > ? ORDER BY valid_from LIMIT 1",
                    [*key, start],
                ).fetchone()
                conn.execute(
                    "UPDATE profile_entry SET valid_to = ? WHERE subject_type=? AND subject_id=? AND attribute=?"  # noqa: E501
                    " AND valid_from < ? AND (valid_to IS NULL OR valid_to > ?)",
                    [start, *key, start, start],
                )
                cur = conn.execute(
                    "INSERT INTO profile_entry (subject_type, subject_id, attribute, value, valid_from, valid_to, source, created_at)"  # noqa: E501
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        *key,
                        payload,
                        start,
                        nxt["valid_from"] if nxt else None,
                        source,
                        to_iso(utcnow()),
                    ],
                )
                entry_id = cur.lastrowid
            row = conn.execute("SELECT * FROM profile_entry WHERE id = ?", [entry_id]).fetchone()
        return _row(row)

    def end(self, subject_type: str, subject_id: str, attribute: str, valid_to: date) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE profile_entry SET valid_to = ? WHERE subject_type=? AND subject_id=? AND attribute=?"  # noqa: E501
                " AND valid_to IS NULL AND valid_from < ?",
                [valid_to.isoformat(), subject_type, subject_id, attribute, valid_to.isoformat()],
            )

    def value_as_of(
        self, subject_type: str, subject_id: str, attribute: str, on: date
    ) -> Any | None:
        day = on.isoformat()
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT value FROM profile_entry WHERE subject_type=? AND subject_id=? AND attribute=?"  # noqa: E501
                " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) ORDER BY valid_from DESC LIMIT 1",  # noqa: E501
                [subject_type, subject_id, attribute, day, day],
            ).fetchone()
        return None if row is None else json.loads(row["value"])

    def as_of(self, subject_type: str, subject_id: str, on: date) -> dict[str, Any]:
        day = on.isoformat()
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT attribute, value FROM profile_entry WHERE subject_type=? AND subject_id=?"
                " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?) ORDER BY attribute, valid_from",  # noqa: E501
                [subject_type, subject_id, day, day],
            ).fetchall()
        return {r["attribute"]: json.loads(r["value"]) for r in rows}

    def history(
        self, subject_type: str, subject_id: str, attribute: str | None = None
    ) -> list[TimelineEntry]:
        sql = "SELECT * FROM profile_entry WHERE subject_type=? AND subject_id=?"
        params: list[Any] = [subject_type, subject_id]
        if attribute is not None:
            sql += " AND attribute = ?"
            params.append(attribute)
        with self.db.connection() as conn:
            rows = conn.execute(sql + " ORDER BY attribute, valid_from", params).fetchall()
        return [_row(r) for r in rows]
