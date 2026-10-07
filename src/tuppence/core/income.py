"""Income sources (salary etc.) with pay rules and upcoming pay dates (spec §5.2, §5.4).

The receiving account is optional: the wizard collects income before accounts and links the
account later. `needs_account` is True while an income has no usable receiving account: none
chosen yet, or one that has since closed or that the person no longer owns (closing an account or
removing an owner lists the incomes this affects, and the UI asks again where they are paid).

Pay dates use the household's nation as of each date for bank holidays (a dated move to Scotland
changes the calendar from then on); England and Wales are assumed where no nation is set.
Status changes follow the shared status rule in `core/records.py`."""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from tuppence.core.accounts import Account, AccountService
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.household import HouseholdService, _check_version
from tuppence.core.money import format_pounds, parse_pounds
from tuppence.core.payrules import NationOn, describe, next_pay_date, parse_rule, pay_dates
from tuppence.core.records import NotFound, update_versioned
from tuppence.core.timeline import HOUSEHOLD_ID, Timeline

Kind = Literal["salary", "self_employment", "benefits", "pension", "rental", "maintenance", "other"]
Component = Literal["bonus", "overtime", "commission"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
_REQUIRED = ("person_id", "kind", "name", "net_amount", "pay_rule")
_FIELDS = (
    "person_id",
    "kind",
    "name",
    "net_amount",
    "account_id",
    "pay_rule",
    "variable_components",
)


class IncomeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: str
    kind: Kind
    name: Name
    net_amount: str
    account_id: str | None = None
    pay_rule: dict[str, Any]
    variable_components: list[Component] = []


class IncomePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_id: str | None = None
    kind: Kind | None = None
    name: Name | None = None
    net_amount: str | None = None
    account_id: str | None = None
    pay_rule: dict[str, Any] | None = None
    variable_components: list[Component] | None = None


class Income(BaseModel):
    id: str
    person_id: str
    kind: Kind
    name: str
    net_amount: str
    account_id: str | None
    pay_rule: dict[str, Any]
    pay_rule_description: str
    variable_components: list[Component]
    next_pay_date: date | None
    needs_account: bool = False
    person_left: bool = False
    calendar_assumed: bool = False
    status: Literal["active", "ended"]
    version: int


class IncomeService:
    def __init__(
        self,
        db: Database,
        household: HouseholdService,
        accounts: AccountService,
        *,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.db = db
        self.household = household
        self.accounts = accounts
        self.today = today

    # -- reads -----------------------------------------------------------------------------
    def _nation(self) -> str | None:
        return self.household.get().nation

    def _nation_on(self) -> NationOn:
        """The household's nation as of any date (the timeline is the source of truth)."""
        return Timeline(self.db).lookup("household", HOUSEHOLD_ID, "nation")

    def calendar_assumed(self) -> bool:
        """True when no nation is set, so England and Wales bank holidays are assumed."""
        return self._nation() is None

    def _context(self) -> tuple[NationOn, set[str], dict[str, Account], bool]:
        """What building an Income needs, read once per request."""
        assumed = self.calendar_assumed()
        people = {p.id for p in self.household.list_people()}
        accounts = {a.id: a for a in self.accounts.list(include_closed=True)}
        return self._nation_on(), people, accounts, assumed

    def _build(
        self,
        r: sqlite3.Row,
        nation_on: NationOn,
        current_people: set[str],
        accounts: dict[str, Account],
        calendar_assumed: bool,
    ) -> Income:
        rule = parse_rule(json.loads(r["pay_rule"]))
        left = r["person_id"] not in current_people
        active = r["status"] == "active" and not left
        nxt = next_pay_date(rule, self.today() - timedelta(days=1), nation_on) if active else None
        into = accounts.get(r["account_id"]) if r["account_id"] else None
        usable = into is not None and into.status == "active" and r["person_id"] in into.owner_ids
        return Income(
            id=r["id"],
            person_id=r["person_id"],
            kind=r["kind"],
            name=r["name"],
            net_amount=format_pounds(r["net_pence"]),
            account_id=r["account_id"],
            pay_rule=rule.model_dump(mode="json"),
            pay_rule_description=describe(rule),
            variable_components=json.loads(r["variable_components"]),
            next_pay_date=nxt,
            needs_account=active and not usable,
            person_left=left,
            calendar_assumed=calendar_assumed,
            status=r["status"],
            version=r["version"],
        )

    def list(self, include_ended: bool = False) -> list[Income]:
        where = "" if include_ended else " WHERE status = 'active'"
        context = self._context()
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM income_source" + where + " ORDER BY created_at, rowid"  # noqa: S608
            ).fetchall()
        built = [self._build(r, *context) for r in rows]
        # People who've left keep their income rows, but they stay out of day-to-day lists.
        return built if include_ended else [i for i in built if not i.person_left]

    def get(self, income_id: str) -> Income:
        context = self._context()
        with self.db.connection() as conn:
            r = conn.execute("SELECT * FROM income_source WHERE id = ?", [income_id]).fetchone()
        if r is None:
            raise NotFound("income", income_id)
        return self._build(r, *context)

    def upcoming(self, after: date, days: int = 35) -> list[tuple[date, Income]]:
        """Pay dates from `after` (inclusive) for `days` days, all active sources, in order."""
        nation = self._nation_on()
        out: list[tuple[date, Income]] = []
        for inc in self.list():
            rule = parse_rule(inc.pay_rule)
            out.extend(
                (d, inc) for d in pay_dates(rule, after, after + timedelta(days=days), nation)
            )
        return sorted(out, key=lambda pair: (pair[0], pair[1].name))

    def preview_rule(self, pay_rule: dict[str, Any]) -> tuple[str, list[date]]:
        """Describe a rule and list its next five dates. Writes nothing."""
        rule = parse_rule(pay_rule)
        start = self.today()
        dates = pay_dates(rule, start, start + timedelta(days=400), self._nation_on())
        return describe(rule), dates[:5]

    # -- validation ------------------------------------------------------------------------
    def _columns(
        self, state: dict[str, Any], *, check_person: bool = True, check_account: bool = True
    ) -> dict[str, Any]:
        """Columns for a whole income state. Edits only check the person and account when
        those changed, so renaming an income whose account has closed still works."""
        if check_person and state["person_id"] not in {p.id for p in self.household.list_people()}:
            raise InputError("Choose a person from your household.")
        pence = parse_pounds(state["net_amount"])
        if state["kind"] == "salary" and pence <= 0:
            raise InputError("Enter what you take home from this job, more than £0.")
        account_id = state.get("account_id")
        if account_id is not None and check_account:
            person = self.household.get_person(state["person_id"])
            try:
                acct = self.accounts.get(account_id)
            except NotFound:
                acct = None
            if acct is None or acct.status != "active" or person.id not in acct.owner_ids:
                raise InputError(f"Choose an account that {person.display_name} owns or shares.")
        rule = parse_rule(state["pay_rule"])
        return {
            "person_id": state["person_id"],
            "kind": state["kind"],
            "name": state["name"],
            "net_pence": pence,
            "account_id": account_id,
            "pay_rule": json.dumps(rule.model_dump(mode="json"), sort_keys=True),
            "variable_components": json.dumps(list(dict.fromkeys(state["variable_components"]))),
        }

    # -- writes ----------------------------------------------------------------------------
    def create(self, data: IncomeIn) -> Income:
        cols = self._columns(data.model_dump())
        income_id = "i_" + secrets.token_hex(4)
        now = to_iso(utcnow())
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        with self.db.transaction() as conn:
            conn.execute(
                f"INSERT INTO income_source (id, {names}, created_at, updated_at) "  # noqa: S608
                f"VALUES (?, {marks}, ?, ?)",
                [income_id, *cols.values(), now, now],
            )
        return self.get(income_id)

    def update(self, income_id: str, changes: dict[str, Any], expected_version: int) -> Income:
        patch = IncomePatch.model_validate(changes)
        data = patch.model_dump(exclude_unset=True)
        for field in _REQUIRED:
            if field in data and data[field] is None:
                raise InputError(f"{field} can't be empty.")
        current = self.get(income_id)
        if current.status == "ended":
            raise InputError("This income has ended. Add a new one instead.")
        state = {f: getattr(current, f) for f in _FIELDS}
        state.update(data)
        person_changed = "person_id" in data and data["person_id"] != current.person_id
        account_changed = "account_id" in data and data["account_id"] != current.account_id
        cols = self._columns(
            state,
            check_person=person_changed,
            check_account=account_changed or (person_changed and state["account_id"] is not None),
        )
        old = self._columns(
            {f: getattr(current, f) for f in _FIELDS}, check_person=False, check_account=False
        )
        changed = {k: v for k, v in cols.items() if old[k] != v}
        if not changed:
            with self.db.connection() as conn:
                _check_version(conn, "income_source", income_id, expected_version)
            return current
        with self.db.transaction() as conn:
            update_versioned(
                conn, "income_source", "id", income_id, expected_version, changed,
                now=to_iso(utcnow()),
            )  # fmt: skip
        return self.get(income_id)

    def end(self, income_id: str, expected_version: int) -> Income:
        if self.get(income_id).status == "ended":
            raise InputError("This income has already ended.")
        with self.db.transaction() as conn:
            update_versioned(
                conn, "income_source", "id", income_id, expected_version, {"status": "ended"},
                now=to_iso(utcnow()),
            )  # fmt: skip
        return self.get(income_id)
