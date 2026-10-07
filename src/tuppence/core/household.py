"""The household and its people (spec §5.2, §5.5)."""

from __future__ import annotations

import re
import secrets
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.records import NotFound, update_versioned

Nation = Literal["england", "wales", "scotland", "northern_ireland"]
Role = Literal["adult", "child", "dependent_adult"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]

_DISTRICT = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?$")
_FULL = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}$")


def normalise_district(value: str) -> str:
    v = value.strip().upper()
    if _FULL.match(v):
        raise InputError("Just the first part of your postcode, please (for example LS6).")
    if not _DISTRICT.match(v):
        raise InputError("That doesn't look like a UK postcode district (for example LS6).")
    return v


def _birth_year_ok(v: int | None) -> int | None:
    if v is not None and not (1900 <= v <= date.today().year):
        raise ValueError("Birth year must be between 1900 and this year.")
    return v


class PersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: Name
    role: Role
    birth_year: int | None = None

    @field_validator("birth_year")
    @classmethod
    def _check_year(cls, v: int | None) -> int | None:
        return _birth_year_ok(v)


class PersonPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: Name | None = None
    role: Role | None = None
    birth_year: int | None = None

    @field_validator("birth_year")
    @classmethod
    def _check_year(cls, v: int | None) -> int | None:
        return _birth_year_ok(v)


class Person(BaseModel):
    id: str
    display_name: str
    role: Role
    birth_year: int | None
    status: Literal["active", "retired"]
    version: int


class HouseholdPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nation: Nation | None = None
    postcode_district: str | None = None
    period_mode: Literal["calendar_month", "pay_cycle"] | None = None
    period_anchor_person_id: str | None = None


class Household(BaseModel):
    nation: Nation | None
    postcode_district: str | None
    currency: str
    period_mode: Literal["calendar_month", "pay_cycle"]
    period_anchor_person_id: str | None
    version: int


class HouseholdService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self) -> Household:
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO household (id, created_at, updated_at) VALUES (1, ?, ?)",
                [now, now],
            )
            row = conn.execute("SELECT * FROM household WHERE id = 1").fetchone()
        return Household(**{k: row[k] for k in Household.model_fields})

    def update(self, changes: HouseholdPatch, expected_version: int) -> Household:
        self.get()
        data = changes.model_dump(exclude_unset=True)
        if data.get("postcode_district") is not None:
            data["postcode_district"] = normalise_district(data["postcode_district"])
        if data.get("period_anchor_person_id") is not None:
            self.get_person(data["period_anchor_person_id"])
        if data:
            with self.db.transaction() as conn:
                update_versioned(
                    conn, "household", "id", 1, expected_version, data, now=to_iso(utcnow())
                )
        return self.get()

    def list_people(self, include_retired: bool = False) -> list[Person]:
        order = " ORDER BY created_at, rowid"
        with self.db.connection() as conn:
            if include_retired:
                rows = conn.execute("SELECT * FROM person" + order).fetchall()  # noqa: S608
            else:
                rows = conn.execute(
                    "SELECT * FROM person WHERE status = 'active'" + order  # noqa: S608
                ).fetchall()
        return [Person(**{k: r[k] for k in Person.model_fields}) for r in rows]

    def get_person(self, person_id: str) -> Person:
        with self.db.connection() as conn:
            r = conn.execute("SELECT * FROM person WHERE id = ?", [person_id]).fetchone()
        if r is None:
            raise NotFound("person", person_id)
        return Person(**{k: r[k] for k in Person.model_fields})

    def create_person(self, data: PersonIn) -> Person:
        person_id = "p_" + secrets.token_hex(4)
        now = to_iso(utcnow())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO person (id, display_name, role, birth_year, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",  # noqa: E501
                [person_id, data.display_name, data.role, data.birth_year, now, now],
            )
        return self.get_person(person_id)

    def update_person(self, person_id: str, changes: PersonPatch, expected_version: int) -> Person:
        data = changes.model_dump(exclude_unset=True)
        if data:
            with self.db.transaction() as conn:
                update_versioned(
                    conn, "person", "id", person_id, expected_version, data, now=to_iso(utcnow())
                )
        return self.get_person(person_id)

    def retire_person(self, person_id: str, expected_version: int) -> Person:
        with self.db.transaction() as conn:
            update_versioned(
                conn,
                "person",
                "id",
                person_id,
                expected_version,
                {"status": "retired"},
                now=to_iso(utcnow()),
            )
        return self.get_person(person_id)
