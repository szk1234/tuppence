"""Bank accounts and credit cards, with one or more owners (spec §5.2)."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.household import HouseholdService, _check_version
from tuppence.core.money import format_pounds, parse_pounds
from tuppence.core.providers_uk import PROVIDERS, provider_name
from tuppence.core.records import NotFound, update_versioned

Kind = Literal["current", "savings", "credit_card"]
Nickname = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
Last4 = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}$")]


def _two_places(v: float) -> float:
    if round(v, 2) != v:
        raise ValueError("Use at most 2 decimal places.")
    return v


Apr = Annotated[float, Field(ge=0, le=100), AfterValidator(_two_places)]
StatementDay = Annotated[int, Field(ge=1, le=31)]

CARD_FIELDS = ("credit_limit", "purchase_apr", "promo_apr", "promo_end", "statement_day")
# Fields that can't be set to null in an edit.
_REQUIRED = ("provider", "kind", "nickname", "owner_ids")
_KNOWN_PROVIDERS = {p.id: p for p in PROVIDERS}


class AccountIn(BaseModel):
    """Field formats only; cross-field rules live in AccountService."""

    model_config = ConfigDict(extra="forbid")
    provider: str
    provider_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None
    ) = None
    kind: Kind
    nickname: Nickname
    last4: Last4 | None = None
    owner_ids: list[str]
    credit_limit: str | None = None
    purchase_apr: Apr | None = None
    promo_apr: Apr | None = None
    promo_end: date | None = None
    statement_day: StatementDay | None = None


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str | None = None
    provider_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None
    ) = None
    kind: Kind | None = None
    nickname: Nickname | None = None
    last4: Last4 | None = None
    owner_ids: list[str] | None = None
    credit_limit: str | None = None
    purchase_apr: Apr | None = None
    promo_apr: Apr | None = None
    promo_end: date | None = None
    statement_day: StatementDay | None = None


class Account(BaseModel):
    id: str
    provider: str
    provider_name: str
    kind: Kind
    nickname: str
    last4: str | None
    owner_ids: list[str]
    credit_limit: str | None
    purchase_apr: float | None
    promo_apr: float | None
    promo_end: date | None
    statement_day: int | None
    status: Literal["active", "closed"]
    version: int
    joint: bool


class AccountService:
    def __init__(self, db: Database, household: HouseholdService) -> None:
        self.db = db
        self.household = household

    # -- reads -----------------------------------------------------------------------------
    def _owners(self, conn: sqlite3.Connection, account_id: str) -> list[str]:
        rows = conn.execute(
            "SELECT o.person_id FROM account_owner o JOIN person p ON p.id = o.person_id "
            "WHERE o.account_id = ? ORDER BY p.created_at, p.rowid",
            [account_id],
        ).fetchall()
        return [r[0] for r in rows]

    def _build(self, conn: sqlite3.Connection, r: sqlite3.Row) -> Account:
        owners = self._owners(conn, r["id"])
        limit = r["credit_limit_pence"]
        return Account(
            id=r["id"],
            provider=r["provider"],
            provider_name=r["provider_name"],
            kind=r["kind"],
            nickname=r["nickname"],
            last4=r["last4"],
            owner_ids=owners,
            credit_limit=None if limit is None else format_pounds(limit),
            purchase_apr=r["purchase_apr"],
            promo_apr=r["promo_apr"],
            promo_end=None if r["promo_end"] is None else date.fromisoformat(r["promo_end"]),
            statement_day=r["statement_day"],
            status=r["status"],
            version=r["version"],
            joint=len(owners) > 1,
        )

    def list(self, include_closed: bool = False) -> list[Account]:
        where = "" if include_closed else " WHERE status = 'active'"
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM account" + where + " ORDER BY created_at, rowid"  # noqa: S608
            ).fetchall()
            return [self._build(conn, r) for r in rows]

    def get(self, account_id: str) -> Account:
        with self.db.connection() as conn:
            r = conn.execute("SELECT * FROM account WHERE id = ?", [account_id]).fetchone()
            if r is None:
                raise NotFound("account", account_id)
            return self._build(conn, r)

    # -- validation ------------------------------------------------------------------------
    def _check_owners(self, owner_ids: list[str], keep: list[str] | None = None) -> list[str]:
        """`keep`: current owners stay even if since retired; added ones must be active."""
        owners = list(dict.fromkeys(owner_ids))
        if not owners:
            raise InputError("Choose at least one owner.")
        allowed = {p.id for p in self.household.list_people()} | set(keep or ())
        if any(o not in allowed for o in owners):
            raise InputError("Choose owners from the people in your household.")
        return owners

    def _columns(self, state: dict[str, Any]) -> dict[str, Any]:
        """Validate the whole account state (cross-field rules) and return its columns."""
        provider = _KNOWN_PROVIDERS.get(state["provider"])
        if provider is None:
            raise InputError("Unknown provider.")
        custom = state.get("provider_name")
        if provider.id == "other":
            if not custom or not custom.strip():
                raise InputError("Tell us the name of the bank or card provider.")
            name = custom.strip()
        else:
            name = provider_name(provider.id)
        if state["kind"] != "credit_card" and any(state.get(f) is not None for f in CARD_FIELDS):
            raise InputError("Only credit cards have a limit, APR or statement day.")
        promo, purchase = state.get("promo_apr"), state.get("purchase_apr")
        if promo is not None and purchase is not None and promo > purchase:
            raise InputError("The promotional rate can't be higher than the purchase rate.")
        limit = state.get("credit_limit")
        return {
            "provider": provider.id,
            "provider_name": name,
            "kind": state["kind"],
            "nickname": state["nickname"],
            "last4": state.get("last4"),
            "credit_limit_pence": None if limit is None else parse_pounds(limit),
            "purchase_apr": state.get("purchase_apr"),
            "promo_apr": state.get("promo_apr"),
            "promo_end": None if state.get("promo_end") is None else state["promo_end"].isoformat(),
            "statement_day": state.get("statement_day"),
        }

    @staticmethod
    def _write_owners(conn: sqlite3.Connection, account_id: str, owners: list[str]) -> None:
        conn.execute("DELETE FROM account_owner WHERE account_id = ?", [account_id])
        conn.executemany(
            "INSERT INTO account_owner (account_id, person_id) VALUES (?, ?)",
            [(account_id, o) for o in owners],
        )

    # -- writes ----------------------------------------------------------------------------
    def create(self, data: AccountIn) -> Account:
        owners = self._check_owners(data.owner_ids)
        cols = self._columns(data.model_dump())
        account_id = "a_" + secrets.token_hex(4)
        now = to_iso(utcnow())
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        with self.db.transaction() as conn:
            conn.execute(
                f"INSERT INTO account (id, {names}, created_at, updated_at) "  # noqa: S608
                f"VALUES (?, {marks}, ?, ?)",
                [account_id, *cols.values(), now, now],
            )
            self._write_owners(conn, account_id, owners)
        return self.get(account_id)

    def update(self, account_id: str, changes: dict[str, Any], expected_version: int) -> Account:
        patch = AccountPatch.model_validate(changes)
        data = patch.model_dump(exclude_unset=True)
        for field in _REQUIRED:
            if field in data and data[field] is None:
                raise InputError(f"{field} can't be empty.")
        current = self.get(account_id)
        owners = None
        if "owner_ids" in data:
            owners = self._check_owners(data["owner_ids"], keep=current.owner_ids)
        state = current.model_dump()
        state["credit_limit"] = current.credit_limit
        state.update({k: v for k, v in data.items() if k != "owner_ids"})
        if "provider" in data and "provider_name" not in data:
            state["provider_name"] = None  # the old custom name belongs to the old provider
        cols = self._columns(state)
        changed = {k: v for k, v in cols.items() if self._differs(k, v, current)}
        owners_changed = owners is not None and set(owners) != set(current.owner_ids)
        if not changed and not owners_changed:
            with self.db.connection() as conn:
                _check_version(conn, "account", account_id, expected_version)
            return self.get(account_id)
        with self.db.transaction() as conn:
            # An owners-only edit still bumps the version: re-assign status to itself.
            update_versioned(
                conn,
                "account",
                "id",
                account_id,
                expected_version,
                changed or {"status": current.status},
                now=to_iso(utcnow()),
            )
            if owners_changed and owners is not None:
                self._write_owners(conn, account_id, owners)
        return self.get(account_id)

    @staticmethod
    def _differs(column: str, value: Any, current: Account) -> bool:
        if column == "credit_limit_pence":
            old = None if current.credit_limit is None else parse_pounds(current.credit_limit)
        elif column == "promo_end":
            old = None if current.promo_end is None else current.promo_end.isoformat()
        else:
            old = getattr(current, column)
        return bool(old != value)

    def _set_status(self, account_id: str, status: str, expected_version: int) -> Account:
        with self.db.transaction() as conn:
            update_versioned(
                conn,
                "account",
                "id",
                account_id,
                expected_version,
                {"status": status},
                now=to_iso(utcnow()),
            )
        return self.get(account_id)

    def close(self, account_id: str, expected_version: int) -> Account:
        return self._set_status(account_id, "closed", expected_version)

    def reopen(self, account_id: str, expected_version: int) -> Account:
        return self._set_status(account_id, "active", expected_version)
