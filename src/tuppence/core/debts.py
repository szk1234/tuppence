"""Debts: loans, car finance (PCP/HP), mortgages, student loans and informal debts either way.

Money is pounds strings at the API and integer pence in storage, including the money inside
`details` (stored as `balloon_pence` / `total_payable_pence`). `details` is validated per kind;
an unknown key is an InputError naming the key. On edit, `details` is shallow-merged into the
stored details (a null value deletes a key). If the kind changes, keys that don't apply to the new
kind are dropped and listed in the response's `details_removed`.

`car_finance_redress_window` is True for broker-arranged car finance whose agreement started
between 2007-04-06 and 2024-11-01 inclusive (spec §11.6); the FCA redress scheme uses it later."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from tuppence.core.accounts import Apr
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.household import HouseholdService, _check_version
from tuppence.core.money import format_pounds, parse_pounds
from tuppence.core.records import NotFound, update_versioned

Kind = Literal[
    "personal_loan",
    "car_finance_pcp",
    "car_finance_hp",
    "mortgage",
    "student_loan",
    "bnpl",
    "overdraft",
    "informal",
    "other",
]
Plan = Literal["plan1", "plan2", "plan4", "plan5", "postgraduate"]
Lender = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
CAR_KINDS = ("car_finance_pcp", "car_finance_hp")
REDRESS_START = date(2007, 4, 6)
REDRESS_END = date(2024, 11, 1)
_NOT_NULL = ("kind", "lender", "balance", "balance_date", "details")
_FIELDS = (
    "kind",
    "lender",
    "person_id",
    "balance",
    "balance_date",
    "apr",
    "monthly_payment",
    "end_date",
    "student_loan_plan",
    "details",
)
_ALLOWED: dict[str, tuple[str, ...]] = {
    "car_finance_pcp": (
        "agreement_start",
        "via_broker",
        "balloon",
        "total_payable",
        "annual_mileage",
    ),
    "car_finance_hp": ("agreement_start", "via_broker", "total_payable"),
    "mortgage": ("fixed_until", "rate_type"),
    "informal": ("direction",),
}
_RATE_TYPES = ("fixed", "tracker", "variable", "svr")
_DIRECTIONS = ("i_owe", "owed_to_me")


class DebtIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    lender: Lender
    person_id: str | None = None
    balance: str
    balance_date: date | None = None  # the service fills it from its clock
    apr: Apr | None = None
    monthly_payment: str | None = None
    end_date: date | None = None
    student_loan_plan: Plan | None = None
    details: dict[str, Any] = {}


class DebtPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind | None = None
    lender: Lender | None = None
    person_id: str | None = None
    balance: str | None = None
    balance_date: date | None = None
    apr: Apr | None = None
    monthly_payment: str | None = None
    end_date: date | None = None
    student_loan_plan: Plan | None = None
    details: dict[str, Any] | None = None


class Debt(BaseModel):
    id: str
    kind: Kind
    lender: str
    person_id: str | None
    balance: str
    balance_date: date
    apr: float | None
    monthly_payment: str | None
    end_date: date | None
    student_loan_plan: Plan | None
    details: dict[str, Any]
    car_finance_redress_window: bool
    details_removed: list[str] = []  # set on an edit that changed kind
    status: Literal["active", "settled"]
    version: int


def _date(key: str, value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(value).isoformat() if isinstance(value, str) else _bad_date(key)
    except ValueError:
        return _bad_date(key)


def _bad_date(key: str) -> str:
    raise InputError(f"'{key}' needs a date like 2024-03-01.")


def _store_details(kind: str, details: dict[str, Any]) -> dict[str, Any]:
    """Validate `details` for the kind and return the stored form (pence, ISO dates)."""
    allowed = _ALLOWED.get(kind, ())
    for key in details:
        if key not in allowed:
            raise InputError(f"'{key}' isn't something we keep for this kind of debt.")
    out: dict[str, Any] = {}
    for key, value in details.items():
        if value is None:
            continue
        if key in ("agreement_start", "fixed_until"):
            out[key] = _date(key, value)
        elif key == "via_broker":
            if not isinstance(value, bool):
                raise InputError("'via_broker' must be yes or no.")
            out[key] = value
        elif key in ("balloon", "total_payable"):
            if not isinstance(value, str):
                what = "balloon payment" if key == "balloon" else "total amount payable"
                raise InputError(f"Enter the {what} as pounds, like 1450.00.")
            out[f"{key}_pence"] = parse_pounds(value)
        elif key == "annual_mileage":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 1_000_000:
                raise InputError("'annual_mileage' must be a whole number of miles.")
            out[key] = value
        elif key == "rate_type":
            if value not in _RATE_TYPES:
                raise InputError("'rate_type' must be fixed, tracker, variable or svr.")
            out[key] = value
        elif key == "direction" and value not in _DIRECTIONS:
            raise InputError("'direction' must be i_owe or owed_to_me.")
        else:
            out[key] = value
    if kind == "informal" and "direction" not in out:
        raise InputError("Say whether you owe this or are owed it ('direction').")
    return out


def _present_details(stored: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in stored.items():
        if key.endswith("_pence"):
            out[key.removesuffix("_pence")] = format_pounds(value)
        else:
            out[key] = value
    return out


def _redress(kind: str, stored: dict[str, Any]) -> bool:
    start = stored.get("agreement_start")
    if kind not in CAR_KINDS or stored.get("via_broker") is not True or start is None:
        return False
    return REDRESS_START <= date.fromisoformat(start) <= REDRESS_END


class DebtService:
    def __init__(
        self,
        db: Database,
        household: HouseholdService,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.db = db
        self.household = household
        self.today = today

    def _build(self, r: sqlite3.Row) -> Debt:
        stored = json.loads(r["details"])
        pay = r["monthly_payment_pence"]
        return Debt(
            id=r["id"],
            kind=r["kind"],
            lender=r["lender"],
            person_id=r["person_id"],
            balance=format_pounds(r["balance_pence"]),
            balance_date=date.fromisoformat(r["balance_date"]),
            apr=r["apr"],
            monthly_payment=None if pay is None else format_pounds(pay),
            end_date=None if r["end_date"] is None else date.fromisoformat(r["end_date"]),
            student_loan_plan=r["student_loan_plan"],
            details=_present_details(stored),
            car_finance_redress_window=_redress(r["kind"], stored),
            status=r["status"],
            version=r["version"],
        )

    def list(self, include_settled: bool = False) -> list[Debt]:
        where = "" if include_settled else " WHERE status = 'active'"
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM debt" + where + " ORDER BY created_at, rowid"  # noqa: S608
            ).fetchall()
        return [self._build(r) for r in rows]

    def get(self, debt_id: str) -> Debt:
        with self.db.connection() as conn:
            r = conn.execute("SELECT * FROM debt WHERE id = ?", [debt_id]).fetchone()
        if r is None:
            raise NotFound("debt", debt_id)
        return self._build(r)

    def total_balance_pence(self) -> int:
        """Active debts you owe; money owed to you doesn't count."""
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT balance_pence, kind, details FROM debt WHERE status = 'active'"
            ).fetchall()
        return sum(
            r["balance_pence"]
            for r in rows
            if not (
                r["kind"] == "informal"
                and json.loads(r["details"]).get("direction") == "owed_to_me"
            )
        )

    def _columns(self, state: dict[str, Any], *, check_person: bool = True) -> dict[str, Any]:
        if (
            check_person
            and state["person_id"] is not None
            and state["person_id"] not in {p.id for p in self.household.list_people()}
        ):
            raise InputError("Choose a person from your household, or leave it as joint.")
        kind, plan = state["kind"], state["student_loan_plan"]
        if kind == "student_loan" and plan is None:
            raise InputError("Choose which student loan plan this is.")
        if kind != "student_loan" and plan is not None:
            raise InputError("A student loan plan only applies to student loans.")
        pay = state["monthly_payment"]
        return {
            "kind": kind,
            "lender": state["lender"],
            "person_id": state["person_id"],
            "balance_pence": parse_pounds(state["balance"]),
            "balance_date": _date("balance_date", state["balance_date"]),
            "apr": state["apr"],
            "monthly_payment_pence": None if pay is None else parse_pounds(pay),
            "end_date": _date("end_date", state["end_date"]),
            "student_loan_plan": plan,
            "details": json.dumps(_store_details(kind, state["details"]), sort_keys=True),
        }

    def create(self, data: DebtIn) -> Debt:
        raw = data.model_dump()
        if raw["balance_date"] is None:
            raw["balance_date"] = self.today()
        cols = self._columns(raw)
        debt_id = "d_" + secrets.token_hex(4)
        now = to_iso(utcnow())
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        with self.db.transaction() as conn:
            conn.execute(
                f"INSERT INTO debt (id, {names}, created_at, updated_at) "  # noqa: S608
                f"VALUES (?, {marks}, ?, ?)",
                [debt_id, *cols.values(), now, now],
            )
        return self.get(debt_id)

    def _state(self, d: Debt) -> dict[str, Any]:
        state = {f: getattr(d, f) for f in _FIELDS}
        state["details"] = dict(d.details)
        return state

    def update(self, debt_id: str, changes: dict[str, Any], expected_version: int) -> Debt:
        patch = DebtPatch.model_validate(changes)
        data = patch.model_dump(exclude_unset=True)
        for field in _NOT_NULL:
            if field in data and data[field] is None:
                raise InputError(f"{field} can't be empty.")
        current = self.get(debt_id)
        if current.status == "settled":
            raise InputError("This debt is settled. Reopen it to make changes.")
        state = self._state(current)
        supplied = data.pop("details", None) or {}
        state.update(data)
        removed: list[str] = []
        merged = {**state["details"], **supplied}
        merged = {k: v for k, v in merged.items() if v is not None}
        if state["kind"] != current.kind:
            allowed = _ALLOWED.get(state["kind"], ())
            removed = [k for k in merged if k not in allowed and k not in supplied]
            merged = {k: v for k, v in merged.items() if k not in removed}
            if state["kind"] != "student_loan" and "student_loan_plan" not in data:
                if state["student_loan_plan"] is not None:
                    removed.append("student_loan_plan")
                state["student_loan_plan"] = None
        state["details"] = merged
        person_changed = "person_id" in data and data["person_id"] != current.person_id
        cols = self._columns(state, check_person=person_changed)
        old = self._columns(self._state(current), check_person=False)
        changed = {k: v for k, v in cols.items() if old[k] != v}
        if not changed:
            with self.db.connection() as conn:
                _check_version(conn, "debt", debt_id, expected_version)
            return current
        with self.db.transaction() as conn:
            update_versioned(
                conn, "debt", "id", debt_id, expected_version, changed, now=to_iso(utcnow())
            )
        return self.get(debt_id).model_copy(update={"details_removed": removed})

    def _set_status(self, debt_id: str, status: str, expected_version: int) -> Debt:
        current = self.get(debt_id)
        if current.status == status:
            word = "settled" if status == "settled" else "open"
            raise InputError(f"This debt is already {word}.")
        with self.db.transaction() as conn:
            update_versioned(
                conn, "debt", "id", debt_id, expected_version, {"status": status},
                now=to_iso(utcnow()),
            )  # fmt: skip
        return self.get(debt_id)

    def settle(self, debt_id: str, expected_version: int) -> Debt:
        return self._set_status(debt_id, "settled", expected_version)

    def reopen(self, debt_id: str, expected_version: int) -> Debt:
        return self._set_status(debt_id, "active", expected_version)
