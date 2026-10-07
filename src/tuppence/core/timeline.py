"""Effective-dated profile attributes (spec §5.3).

Intervals are [valid_from, valid_to): valid_to is exclusive and NULL means "still true".

The timeline is the source of truth for the household's nation and postcode district (ruling
R14). The `household` row keeps their value as of today as a cache: every write here or through
HouseholdService keeps the two in step, in the same transaction.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.money import MAX_PENCE, parse_pounds
from tuppence.core.postcode import normalise_district
from tuppence.core.records import NotFound, VersionConflict

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
        "housing_tenure": TypeAdapter(
            Literal["renting", "mortgage", "owned", "living_with_family"]
        ),
        "housing_monthly_pence": TypeAdapter(Annotated[int, Field(ge=0, le=MAX_PENCE)]),
        "bedrooms": TypeAdapter(Annotated[int, Field(ge=0, le=20)]),
        "council_tax_band": TypeAdapter(Literal["A", "B", "C", "D", "E", "F", "G", "H", "I"]),
    },
}


# Money attributes: sent as pounds strings, stored as integer pence (never a bare number).
POUNDS_ATTRIBUTES = frozenset({"housing_monthly_pence"})

HOUSEHOLD_ID = "1"
# Household attributes whose current value the household row caches (column names).
HOUSEHOLD_ROW_FIELDS = ("nation", "postcode_district")


class TimelineEntry(BaseModel):
    id: int
    subject_type: str
    subject_id: str
    attribute: str
    value: Any
    valid_from: date
    valid_to: date | None
    source: str
    version: int = 1


def _validate(subject_type: str, attribute: str, value: Any) -> Any:
    adapters = ALLOWED_ATTRIBUTES.get(subject_type, {})
    if attribute not in adapters:
        raise InputError(f"'{attribute}' can't be recorded for a {subject_type}.")
    if attribute in POUNDS_ATTRIBUTES:
        # Callers send pounds ("1450.00"); the timeline stores integer pence.
        if not isinstance(value, str):
            raise InputError("Enter an amount like 1450 or 1,450.50.")
        value = parse_pounds(value)
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
        version=row["version"],
    )


_KEY = "subject_type=? AND subject_id=? AND attribute=?"


def _value_on(conn: sqlite3.Connection, key: list[str], day: str) -> Any | None:
    row = conn.execute(
        f"SELECT value FROM profile_entry WHERE {_KEY}"  # noqa: S608 - constant clause
        " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
        " ORDER BY valid_from DESC LIMIT 1",
        [*key, day, day],
    ).fetchone()
    return None if row is None else json.loads(row["value"])


def _put(conn: sqlite3.Connection, key: list[str], payload: str, start: str, source: str) -> int:
    """Record a value from `start`; it lasts until the next recorded change (if any)."""
    same = conn.execute(
        f"SELECT id FROM profile_entry WHERE {_KEY} AND valid_from=?",  # noqa: S608
        [*key, start],
    ).fetchone()
    if same is not None:
        conn.execute(
            "UPDATE profile_entry SET value = ?, source = ?, version = version + 1 WHERE id = ?",
            [payload, source, same["id"]],
        )
        return int(same["id"])
    nxt = conn.execute(
        f"SELECT valid_from FROM profile_entry WHERE {_KEY}"  # noqa: S608
        " AND valid_from > ? ORDER BY valid_from LIMIT 1",
        [*key, start],
    ).fetchone()
    conn.execute(
        f"UPDATE profile_entry SET valid_to = ? WHERE {_KEY}"  # noqa: S608
        " AND valid_from < ? AND (valid_to IS NULL OR valid_to > ?)",
        [start, *key, start, start],
    )
    cur = conn.execute(
        "INSERT INTO profile_entry (subject_type, subject_id, attribute, value, valid_from,"
        " valid_to, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [*key, payload, start, nxt["valid_from"] if nxt else None, source, to_iso(utcnow())],
    )
    return int(cur.lastrowid or 0)


def _clear_from(conn: sqlite3.Connection, key: list[str], day: str) -> None:
    """No value from `day` until the next recorded change."""
    conn.execute(f"DELETE FROM profile_entry WHERE {_KEY} AND valid_from = ?", [*key, day])  # noqa: S608
    conn.execute(
        f"UPDATE profile_entry SET valid_to = ? WHERE {_KEY}"  # noqa: S608
        " AND valid_from < ? AND (valid_to IS NULL OR valid_to > ?)",
        [day, *key, day, day],
    )


def record_household_change(
    conn: sqlite3.Connection, attribute: str, value: Any, today: date, *, source: str = "user"
) -> None:
    """Record a household nation/district edit as true from today (None clears it from today)."""
    key = ["household", HOUSEHOLD_ID, attribute]
    day = today.isoformat()
    clean = None if value is None else _validate("household", attribute, value)
    if _value_on(conn, key, day) == clean:
        return  # unchanged: don't split the current interval
    if clean is None:
        _clear_from(conn, key, day)
    else:
        _put(conn, key, json.dumps(clean), day, source)


def sync_household_row(conn: sqlite3.Connection, today: date, *, now: str) -> None:
    """Make the household row's nation/district match the timeline as of `today`.

    An attribute with no value today (no history, only future entries, or every entry deleted)
    becomes NULL. A change bumps the row's version, so a tab holding
    the old version gets a 409 instead of overwriting.
    """
    conn.execute(
        "INSERT OR IGNORE INTO household (id, created_at, updated_at) VALUES (1, ?, ?)", [now, now]
    )
    row = conn.execute("SELECT nation, postcode_district FROM household WHERE id = 1").fetchone()
    day = today.isoformat()
    changes: dict[str, Any] = {}
    for attribute in HOUSEHOLD_ROW_FIELDS:
        value = _value_on(conn, ["household", HOUSEHOLD_ID, attribute], day)
        if row[attribute] != value:
            changes[attribute] = value
    if changes:
        assignments = ", ".join(f"{column} = ?" for column in changes)  # from HOUSEHOLD_ROW_FIELDS
        conn.execute(
            f"UPDATE household SET {assignments}, version = version + 1, updated_at = ?"  # noqa: S608
            " WHERE id = 1",
            [*changes.values(), now],
        )


_UNSET: Any = object()


def _current_version(conn: sqlite3.Connection, key: list[str], day: date) -> int:
    row = conn.execute(
        f"SELECT version FROM profile_entry WHERE {_KEY}"  # noqa: S608 - constant clause
        " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
        " ORDER BY valid_from DESC LIMIT 1",
        [*key, day.isoformat(), day.isoformat()],
    ).fetchone()
    return 0 if row is None else int(row["version"])


class Timeline:
    def __init__(self, db: Database, *, today: Callable[[], date] = date.today) -> None:
        self.db = db
        self.today = today

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
        expected_current: Any = _UNSET,
    ) -> TimelineEntry:
        """Record a value from `valid_from`.

        With `expected_current` (the value the caller believes is current on `valid_from`, or
        None for nothing), a different stored value raises `VersionConflict`.
        """
        clean = _validate(subject_type, attribute, value)
        self._require_subject(subject_type, subject_id)
        key = [subject_type, subject_id, attribute]
        with self.db.transaction() as conn:
            if expected_current is not _UNSET:
                expected = (
                    None
                    if expected_current is None
                    else _validate(subject_type, attribute, expected_current)
                )
                if _value_on(conn, key, valid_from.isoformat()) != expected:
                    raise VersionConflict(
                        "profile_entry", tuple(key), 0, _current_version(conn, key, valid_from)
                    )
            entry_id = _put(conn, key, json.dumps(clean), valid_from.isoformat(), source)
            if subject_type == "household":
                sync_household_row(conn, self.today(), now=to_iso(utcnow()))
            row = conn.execute("SELECT * FROM profile_entry WHERE id = ?", [entry_id]).fetchone()
        return _row(row)

    def delete(self, entry_id: int, expected_version: int) -> None:
        """Remove one entry; the one before it carries on until the next recorded change."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM profile_entry WHERE id = ?", [entry_id]).fetchone()
            if row is None:
                raise NotFound("profile_entry", entry_id)
            if row["version"] != expected_version:
                raise VersionConflict("profile_entry", entry_id, expected_version, row["version"])
            conn.execute("DELETE FROM profile_entry WHERE id = ?", [entry_id])
            conn.execute(
                "UPDATE profile_entry SET valid_to = ?"
                " WHERE subject_type=? AND subject_id=? AND attribute=? AND valid_to = ?",
                [
                    row["valid_to"],
                    row["subject_type"],
                    row["subject_id"],
                    row["attribute"],
                    row["valid_from"],
                ],
            )
            if row["subject_type"] == "household":
                sync_household_row(conn, self.today(), now=to_iso(utcnow()))

    def end(self, subject_type: str, subject_id: str, attribute: str, valid_to: date) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE profile_entry SET valid_to = ? WHERE subject_type=? AND subject_id=? AND attribute=?"  # noqa: E501
                " AND valid_to IS NULL AND valid_from < ?",
                [valid_to.isoformat(), subject_type, subject_id, attribute, valid_to.isoformat()],
            )
            if subject_type == "household":
                sync_household_row(conn, self.today(), now=to_iso(utcnow()))

    def value_as_of(
        self, subject_type: str, subject_id: str, attribute: str, on: date
    ) -> Any | None:
        with self.db.connection() as conn:
            return _value_on(conn, [subject_type, subject_id, attribute], on.isoformat())

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
            rows = conn.execute(sql + " ORDER BY valid_from, attribute, id", params).fetchall()
        return [_row(r) for r in rows]
