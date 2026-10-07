"""Savings goals. Money is pounds strings at the API and integer pence in storage.

A target date must be in the future when it is set or changed; an existing goal whose date
has passed can still be renamed or have its saved amount updated. Status changes and edits follow
the shared status rule in `core/records.py`."""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.household import _check_version
from tuppence.core.money import format_pounds, parse_pounds
from tuppence.core.records import NotFound, update_versioned

Kind = Literal[
    "emergency_fund",
    "house_deposit",
    "holiday",
    "car",
    "wedding",
    "education",
    "retirement",
    "debt_free",
    "other",
]
Status = Literal["active", "achieved", "abandoned"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
_NOT_NULL = ("name", "kind", "saved_amount", "priority")
_FIELDS = ("name", "kind", "target_amount", "saved_amount", "target_date", "priority")


class GoalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    kind: Kind
    target_amount: str | None = None
    saved_amount: str = "0"
    target_date: date | None = None
    priority: Literal[1, 2, 3] = 2


class GoalPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    kind: Kind | None = None
    target_amount: str | None = None
    saved_amount: str | None = None
    target_date: date | None = None
    priority: Literal[1, 2, 3] | None = None


class Goal(BaseModel):
    id: str
    name: str
    kind: Kind
    target_amount: str | None
    saved_amount: str
    target_date: date | None
    priority: int
    status: Status
    version: int


class GoalService:
    def __init__(self, db: Database, *, today: Callable[[], date] = date.today) -> None:
        self.db = db
        self.today = today

    def _build(self, r: sqlite3.Row) -> Goal:
        target = r["target_pence"]
        return Goal(
            id=r["id"],
            name=r["name"],
            kind=r["kind"],
            target_amount=None if target is None else format_pounds(target),
            saved_amount=format_pounds(r["saved_pence"]),
            target_date=None if r["target_date"] is None else date.fromisoformat(r["target_date"]),
            priority=r["priority"],
            status=r["status"],
            version=r["version"],
        )

    def list(self, include_closed: bool = False) -> list[Goal]:
        where = "" if include_closed else " WHERE status = 'active'"
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM goal" + where + " ORDER BY priority, created_at, rowid"  # noqa: S608
            ).fetchall()
        return [self._build(r) for r in rows]

    def get(self, goal_id: str) -> Goal:
        with self.db.connection() as conn:
            r = conn.execute("SELECT * FROM goal WHERE id = ?", [goal_id]).fetchone()
        if r is None:
            raise NotFound("goal", goal_id)
        return self._build(r)

    def suggest_emergency_fund(self) -> bool:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM goal WHERE kind = 'emergency_fund' AND status = 'active' LIMIT 1"
            ).fetchone()
        return row is None

    def _columns(self, state: dict[str, Any], *, check_date: bool) -> dict[str, Any]:
        target = state["target_amount"]
        target_pence = None if target is None else parse_pounds(target)
        if target_pence is not None and target_pence <= 0:
            raise InputError("Enter a target of more than £0, or leave it blank.")
        when: date | None = state["target_date"]
        if check_date and when is not None and when <= self.today():
            raise InputError("Choose a target date in the future.")
        return {
            "name": state["name"],
            "kind": state["kind"],
            "target_pence": target_pence,
            "saved_pence": parse_pounds(state["saved_amount"]),
            "target_date": None if when is None else when.isoformat(),
            "priority": state["priority"],
        }

    def create(self, data: GoalIn) -> Goal:
        cols = self._columns(data.model_dump(), check_date=True)
        goal_id = "g_" + secrets.token_hex(4)
        now = to_iso(utcnow())
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        with self.db.transaction() as conn:
            conn.execute(
                f"INSERT INTO goal (id, {names}, created_at, updated_at) "  # noqa: S608
                f"VALUES (?, {marks}, ?, ?)",
                [goal_id, *cols.values(), now, now],
            )
        return self.get(goal_id)

    def update(self, goal_id: str, changes: dict[str, Any], expected_version: int) -> Goal:
        patch = GoalPatch.model_validate(changes)
        data = patch.model_dump(exclude_unset=True)
        for field in _NOT_NULL:
            if field in data and data[field] is None:
                raise InputError(f"{field} can't be empty.")
        current = self.get(goal_id)
        if current.status != "active":
            word = "achieved" if current.status == "achieved" else "dropped"
            raise InputError(f"This goal is {word}. Reopen it to make changes.")
        state = {f: getattr(current, f) for f in _FIELDS}
        state.update(data)
        date_changed = "target_date" in data and data["target_date"] != current.target_date
        cols = self._columns(state, check_date=date_changed)
        old = self._columns({f: getattr(current, f) for f in _FIELDS}, check_date=False)
        changed = {k: v for k, v in cols.items() if old[k] != v}
        if not changed:
            with self.db.connection() as conn:
                _check_version(conn, "goal", goal_id, expected_version)
            return current
        with self.db.transaction() as conn:
            update_versioned(
                conn, "goal", "id", goal_id, expected_version, changed, now=to_iso(utcnow())
            )
        return self.get(goal_id)

    def set_status(self, goal_id: str, status: str, expected_version: int) -> Goal:
        if status not in ("active", "achieved", "abandoned"):
            raise InputError("Choose active, achieved or abandoned.")
        if self.get(goal_id).status == status:
            word = {"active": "active", "achieved": "achieved", "abandoned": "dropped"}[status]
            raise InputError(f"This goal is already {word}.")
        with self.db.transaction() as conn:
            update_versioned(
                conn, "goal", "id", goal_id, expected_version, {"status": status},
                now=to_iso(utcnow()),
            )  # fmt: skip
        return self.get(goal_id)
