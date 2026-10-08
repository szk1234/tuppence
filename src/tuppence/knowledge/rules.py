"""Rules: "payments like this are that", applied in code with no AI (spec §7).

A rule matches on any mix of merchant, a phrase in the description, an amount range,
an account, the direction, a date range and an account holder. When several rules
match, the most specific wins. M4 applies rules deterministically; learning new rules
from feedback is M5's Learner.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.money import format_pounds
from tuppence.core.records import NotFound, update_versioned
from tuppence.knowledge.authority import CODE_RULE, USER_RULE
from tuppence.knowledge.models import HOUSEHOLD, Decision
from tuppence.knowledge.understanding import TRANSFER_CATEGORY, UnderstandingStore
from tuppence.knowledge.versions import KnowledgeVersions

RuleSource = Literal["user", "learned", "seed"]

# Generic UK patterns every household shares. They are rules like any other: the person
# can switch them off.
SEED_RULES: list[tuple[str, str, Literal["in", "out"], str]] = [
    ("seed-council-tax", "COUNCIL TAX", "out", "housing.council-tax"),
    ("seed-tv-licence", "TV LICENCE", "out", "housing.tv-licence"),
    ("seed-tv-licensing", "TV LICENSING", "out", "housing.tv-licence"),
    ("seed-dvla", "DVLA", "out", "transport.car.road-tax"),
    ("seed-cash-machine", "CASH MACHINE", "out", "transfers.cash"),
    ("seed-cash-withdrawal", "CASH WITHDRAWAL", "out", "transfers.cash"),
    ("seed-atm", "ATM", "out", "transfers.cash"),
    ("seed-child-benefit", "CHILD BENEFIT", "in", "income.benefits"),
    ("seed-universal-credit", "UNIVERSAL CREDIT", "in", "income.benefits"),
    ("seed-dwp", "DWP", "in", "income.benefits"),
    ("seed-hmrc", "HMRC", "out", "financial.tax"),
    ("seed-non-sterling-fee", "NON-STERLING", "out", "financial.bank-fees"),
    ("seed-interest-earned", "GROSS INTEREST", "in", "income.interest"),
]


@dataclass(frozen=True)
class TxnFacts:
    """What a rule can look at for one transaction."""

    id: str
    account_id: str
    account_kind: str
    date: date
    amount_pence: int
    text: str  # the raw description and the merchant text, upper case
    merchant_id: str | None
    owner_ids: frozenset[str]
    bank_type: str | None = None


def load_txn_facts(conn: sqlite3.Connection, ids: Sequence[str] | None = None) -> list[TxnFacts]:
    owners: dict[str, set[str]] = {}
    for row in conn.execute("SELECT account_id, person_id FROM account_owner"):
        owners.setdefault(row["account_id"], set()).add(row["person_id"])
    sql = (
        "SELECT t.id, t.account_id, a.kind AS account_kind, t.date, t.amount_pence,"
        " t.raw_description, t.merchant_text, t.bank_type, u.merchant_id"
        ' FROM "transaction" t JOIN account a ON a.id = t.account_id'
        " JOIN understanding u ON u.transaction_id = t.id"
    )
    if ids is None:
        rows = conn.execute(sql + " ORDER BY t.date, t.id").fetchall()
    else:
        rows = conn.execute(
            sql + " WHERE t.id IN (SELECT value FROM json_each(?)) ORDER BY t.date, t.id",  # noqa: S608
            [json.dumps(list(ids))],
        ).fetchall()
    return [
        TxnFacts(
            id=r["id"],
            account_id=r["account_id"],
            account_kind=r["account_kind"],
            date=date.fromisoformat(r["date"]),
            amount_pence=r["amount_pence"],
            text=" ".join(f"{r['raw_description']} {r['merchant_text'] or ''}".upper().split()),
            merchant_id=r["merchant_id"],
            owner_ids=frozenset(owners.get(r["account_id"], set())),
            bank_type=r["bank_type"],
        )
        for r in rows
    ]


_WORD = re.compile(r"[A-Z0-9]+")


def phrase_in(pattern: str, text: str) -> bool:
    """True when the pattern's words appear together, in order, as whole words.
    "ATM" matches "ATM WITHDRAWAL" but not "TREATMENT"."""
    want = _WORD.findall(pattern.upper())
    have = _WORD.findall(text.upper())
    if not want:
        return False
    return any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1))


class RuleIn(BaseModel):
    """A rule as the person (or the seed list) states it. Amounts are pence, unsigned."""

    merchant_id: str | None = None
    text_pattern: str | None = Field(default=None, min_length=2, max_length=100)
    min_amount_pence: int | None = Field(default=None, gt=0)
    max_amount_pence: int | None = Field(default=None, gt=0)
    account_id: str | None = None
    direction: Literal["in", "out"] | None = None
    date_from: date | None = None
    date_to: date | None = None
    person_id: str | None = None
    set_category_id: str | None = None
    set_who: str | None = None
    set_transfer: bool | None = None
    set_ignore: bool = False

    @model_validator(mode="after")
    def _shape(self) -> RuleIn:
        if not any(
            [
                self.merchant_id,
                self.text_pattern,
                self.account_id,
                self.person_id,
                self.min_amount_pence,
                self.max_amount_pence,
            ]
        ):
            raise ValueError(
                "A rule needs a merchant, words to look for, an account, a person or an amount."
            )
        if not (self.set_category_id or self.set_transfer or self.set_ignore):
            raise ValueError("A rule needs to set a category, mark a transfer, or hide payments.")
        if (
            self.min_amount_pence
            and self.max_amount_pence
            and self.min_amount_pence > self.max_amount_pence
        ):
            raise ValueError("The smallest amount must not be more than the largest.")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("The start date must be before the end date.")
        return self


class Rule(RuleIn):
    id: str
    description: str
    source: RuleSource
    confirmed: bool
    enabled: bool
    hit_count: int
    created_at: str
    version: int


def matches(rule: RuleIn, txn: TxnFacts) -> bool:
    size = abs(txn.amount_pence)
    checks = (
        rule.merchant_id is None or rule.merchant_id == txn.merchant_id,
        rule.text_pattern is None or phrase_in(rule.text_pattern, txn.text),
        rule.min_amount_pence is None or size >= rule.min_amount_pence,
        rule.max_amount_pence is None or size <= rule.max_amount_pence,
        rule.account_id is None or rule.account_id == txn.account_id,
        rule.direction is None or (rule.direction == "in") == (txn.amount_pence > 0),
        rule.date_from is None or txn.date >= rule.date_from,
        rule.date_to is None or txn.date <= rule.date_to,
        rule.person_id is None or rule.person_id in txn.owner_ids,
    )
    return all(checks)


def specificity(rule: RuleIn) -> int:
    return (
        4 * bool(rule.merchant_id)
        + 3 * bool(rule.text_pattern)
        + 2 * bool(rule.account_id)
        + 2 * bool(rule.min_amount_pence or rule.max_amount_pence)
        + bool(rule.person_id)
        + bool(rule.direction)
        + bool(rule.date_from or rule.date_to)
    )


def best_rule(rules: Iterable[Rule], txn: TxnFacts) -> Rule | None:
    """The most specific enabled rule that matches; the person's rules beat seed rules,
    then the newest wins."""
    found = [r for r in rules if r.enabled and matches(r, txn)]
    if not found:
        return None
    return max(found, key=lambda r: (specificity(r), r.source != "seed", r.created_at, r.id))


def rule_decision(rule: Rule, *, category_kind: str | None) -> Decision:
    category = rule.set_category_id or (TRANSFER_CATEGORY if rule.set_transfer else None)
    is_transfer = rule.set_transfer
    if is_transfer is None and category is not None:
        is_transfer = category_kind == "transfer"
    return Decision(
        decided_by="rule",
        authority=CODE_RULE if rule.source == "seed" else USER_RULE,
        status="inferred",
        confidence=1.0,
        category_id=category,
        who=rule.set_who,
        is_transfer=is_transfer,
        ignored=True if rule.set_ignore else None,
        rule_id=rule.id,
        evidence={"rule": rule.description},
    )


class RulePreview(BaseModel):
    matches: int
    will_change: int
    kept_yours: int  # rows the person set themselves: a rule never changes them
    examples: list[dict[str, str]]


def _rule(row: sqlite3.Row) -> Rule:
    data = dict(row)
    for key in ("confirmed", "enabled", "set_ignore"):
        data[key] = bool(data[key])
    data["set_transfer"] = None if data["set_transfer"] is None else bool(data["set_transfer"])
    for key in ("date_from", "date_to"):
        data[key] = date.fromisoformat(data[key]) if data[key] else None
    return Rule.model_validate({k: v for k, v in data.items() if k in Rule.model_fields})


class RuleStore:
    def __init__(
        self,
        db: Database,
        versions: KnowledgeVersions,
        understanding: UnderstandingStore,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.db, self.versions, self.understanding, self.clock = db, versions, understanding, clock

    # --- reading -------------------------------------------------------------------------

    def list(self, *, include_disabled: bool = False) -> list[Rule]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM rule WHERE enabled = 1 OR ? ORDER BY created_at, id",
                [int(include_disabled)],
            )
            return [_rule(r) for r in rows]

    @staticmethod
    def active_in(conn: sqlite3.Connection) -> list[Rule]:
        return [_rule(r) for r in conn.execute("SELECT * FROM rule WHERE enabled = 1")]

    def get(self, rule_id: str) -> Rule:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM rule WHERE id = ?", [rule_id]).fetchone()
        if row is None:
            raise NotFound("rule", rule_id)
        return _rule(row)

    # --- writing -------------------------------------------------------------------------

    def seed(self) -> int:
        now = to_iso(self.clock())
        added = 0
        with self.db.transaction() as conn:
            for rule_id, pattern, direction, category in SEED_RULES:
                label = conn.execute(
                    "SELECT label FROM category WHERE id = ?", [category]
                ).fetchone()
                if label is None:
                    continue
                way = "Money in" if direction == "in" else "Payments"
                cur = conn.execute(
                    "INSERT OR IGNORE INTO rule (id, description, text_pattern, direction,"
                    " set_category_id, source, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 'seed', ?, ?)",
                    [
                        rule_id,
                        f"{way} mentioning “{pattern}” → {label['label']}",
                        pattern,
                        direction,
                        category,
                        now,
                        now,
                    ],
                )
                added += cur.rowcount
        return added

    def _describe(self, conn: sqlite3.Connection, rule: RuleIn) -> str:
        def name(sql: str, key: str | None) -> str | None:
            if key is None:
                return None
            row = conn.execute(sql, [key]).fetchone()
            if row is None:
                raise InputError("Something this rule refers to no longer exists.")
            return str(row[0])

        parts = ["Money in" if rule.direction == "in" else "Payments"]
        if merchant := name("SELECT name FROM merchant WHERE id = ?", rule.merchant_id):
            parts.append(("from " if rule.direction == "in" else "to ") + merchant)
        if rule.text_pattern:
            parts.append(f"mentioning “{rule.text_pattern.upper()}”")
        if account := name("SELECT nickname FROM account WHERE id = ?", rule.account_id):
            parts.append(f"on {account}")
        if person := name("SELECT display_name FROM person WHERE id = ?", rule.person_id):
            parts.append(f"on {person}'s accounts")
        low, high = rule.min_amount_pence, rule.max_amount_pence
        if low and high:
            parts.append(f"of £{format_pounds(low)} to £{format_pounds(high)}")
        elif low:
            parts.append(f"of £{format_pounds(low)} or more")
        elif high:
            parts.append(f"of up to £{format_pounds(high)}")
        if rule.date_from or rule.date_to:
            start = rule.date_from.strftime("%d/%m/%Y") if rule.date_from else "the start"
            end = rule.date_to.strftime("%d/%m/%Y") if rule.date_to else "now"
            parts.append(f"from {start} to {end}")
        actions: list[str] = []
        if rule.set_category_id:
            labels = conn.execute(
                "WITH RECURSIVE up(id, parent_id, label, level) AS ("
                " SELECT id, parent_id, label, level FROM category WHERE id = ?"
                " UNION ALL SELECT c.id, c.parent_id, c.label, c.level FROM category c"
                " JOIN up ON c.id = up.parent_id) SELECT label FROM up ORDER BY level",
                [rule.set_category_id],
            ).fetchall()
            if not labels:
                raise InputError("Choose a category from the list.")
            actions.append(" › ".join(r[0] for r in labels))
        elif rule.set_transfer:
            actions.append("transfers between your accounts")
        if rule.set_who:
            who = (
                "everyone"
                if rule.set_who == HOUSEHOLD
                else name("SELECT display_name FROM person WHERE id = ?", rule.set_who)
            )
            actions.append(f"for {who}")
        if rule.set_ignore:
            actions.append("left out of spending")
        return f"{' '.join(parts)} → {', '.join(actions)}"

    def create_in(
        self,
        conn: sqlite3.Connection,
        rule: RuleIn,
        *,
        source: RuleSource = "user",
        created_from_transaction_id: str | None = None,
    ) -> Rule:
        if rule.set_category_id is not None:
            row = conn.execute(
                "SELECT retired FROM category WHERE id = ?", [rule.set_category_id]
            ).fetchone()
            if row is None or row["retired"]:
                raise InputError("Choose a category from the list.")
        description = self._describe(conn, rule)
        rule_id = "r_" + secrets.token_hex(6)
        now = to_iso(self.clock())
        conn.execute(
            "INSERT INTO rule (id, description, merchant_id, text_pattern, min_amount_pence,"
            " max_amount_pence, account_id, direction, date_from, date_to, person_id,"
            " set_category_id, set_who, set_transfer, set_ignore, source, confirmed,"
            " created_from_transaction_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            [
                rule_id,
                description,
                rule.merchant_id,
                rule.text_pattern.upper() if rule.text_pattern else None,
                rule.min_amount_pence,
                rule.max_amount_pence,
                rule.account_id,
                rule.direction,
                rule.date_from.isoformat() if rule.date_from else None,
                rule.date_to.isoformat() if rule.date_to else None,
                rule.person_id,
                rule.set_category_id,
                rule.set_who,
                None if rule.set_transfer is None else int(rule.set_transfer),
                int(rule.set_ignore),
                source,
                created_from_transaction_id,
                now,
                now,
            ],
        )
        self.versions.bump(
            conn, "rule", merchant_id=rule.merchant_id, rule_id=rule_id, note=description
        )
        return _rule(conn.execute("SELECT * FROM rule WHERE id = ?", [rule_id]).fetchone())

    def preview(self, rule: RuleIn) -> RulePreview:
        """What applying this rule to past transactions would do, without doing it."""
        with self.db.connection() as conn:
            facts = [f for f in load_txn_facts(conn) if matches(rule, f)]
            rows = self.understanding.many_in(conn, [f.id for f in facts])
            target = rule.set_category_id or (TRANSFER_CATEGORY if rule.set_transfer else None)
            kept = [f for f in facts if rows[f.id].status == "confirmed"]
            same = [
                f
                for f in facts
                if rows[f.id].status != "confirmed"
                and (target is None or rows[f.id].category_id == target)
                and (not rule.set_ignore or rows[f.id].ignored)
            ]
            examples = [
                {
                    "date": f.date.isoformat(),
                    "description": f.text[:60],
                    "amount": format_pounds(f.amount_pence),
                    "category_id": rows[f.id].category_id or "",
                }
                for f in sorted(facts, key=lambda f: f.date, reverse=True)[:5]
            ]
        return RulePreview(
            matches=len(facts),
            will_change=len(facts) - len(kept) - len(same),
            kept_yours=len(kept),
            examples=examples,
        )

    def apply_in(self, conn: sqlite3.Connection, rule: Rule, *, actor: str = "rules") -> int:
        """Apply a rule to every past transaction it matches and that it is the best rule
        for. Returns how many understanding rows changed."""
        active = self.active_in(conn)
        kinds = {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM category")}
        version = self.versions.current_in(conn)
        changed = 0
        for facts in load_txn_facts(conn):
            if not matches(rule, facts):
                continue
            best = best_rule(active, facts)
            if best is None or best.id != rule.id:
                continue
            decision = rule_decision(rule, category_kind=kinds.get(rule.set_category_id or ""))
            if self.understanding.apply(
                conn,
                facts.id,
                decision,
                actor=actor,
                knowledge_version=version,
                reason=rule.description,
            ):
                changed += 1
        if changed:
            self.record_hits(conn, {rule.id: changed})
        return changed

    def create(
        self,
        rule: RuleIn,
        *,
        apply_to_past: bool = True,
        created_from_transaction_id: str | None = None,
    ) -> tuple[Rule, int]:
        with self.db.transaction() as conn:
            created = self.create_in(
                conn, rule, created_from_transaction_id=created_from_transaction_id
            )
            changed = self.apply_in(conn, created) if apply_to_past else 0
        return self.get(created.id), changed

    def disable(self, rule_id: str, expected_version: int) -> tuple[Rule, int]:
        """Switch a rule off. Rows it decided go back to the queue to be looked at again."""
        with self.db.transaction() as conn:
            update_versioned(
                conn,
                "rule",
                "id",
                rule_id,
                expected_version,
                {"enabled": 0},
                now=to_iso(self.clock()),
            )
            rule = _rule(conn.execute("SELECT * FROM rule WHERE id = ?", [rule_id]).fetchone())
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT transaction_id FROM understanding WHERE rule_id = ?"
                    " AND status != 'confirmed'",
                    [rule_id],
                )
            ]
            for transaction_id in ids:
                self.understanding.release(
                    conn,
                    transaction_id,
                    actor="rules",
                    reason=f"rule switched off: {rule.description}",
                )
            self.versions.bump(
                conn, "rule", merchant_id=rule.merchant_id, rule_id=rule_id, note="switched off"
            )
        return self.get(rule_id), len(ids)

    @staticmethod
    def record_hits(conn: sqlite3.Connection, hits: dict[str, int]) -> None:
        for rule_id, count in hits.items():
            conn.execute(
                "UPDATE rule SET hit_count = hit_count + ?, last_hit_at = ? WHERE id = ?",
                [count, to_iso(utcnow()), rule_id],
            )
