"""The Transfer matcher (spec §8.2): pairs money moving between the household's own
accounts, and credit card repayments. Code only, no AI.

A pair is two transactions on different accounts with opposite signs and exactly the same
amount, at most `max_days_apart` days apart, at least one of them new in this run. When
several pairings are possible the closest dates win, then the one whose descriptions say
"transfer" (or name the other account). A payment that names another of the household's
accounts whose statement for those dates hasn't been uploaded is marked as a one-sided
transfer.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from tuppence.config.models import AgentManifest
from tuppence.core.db import Database
from tuppence.knowledge.authority import CODE_RULE
from tuppence.knowledge.models import HOUSEHOLD, Decision
from tuppence.knowledge.understanding import UnderstandingStore
from tuppence.knowledge.versions import KnowledgeVersions

NAME = "transfer_matcher"
CARD_REPAYMENT = "transfers.card-repayment"
BETWEEN_ACCOUNTS = "transfers.between-accounts"
TRANSFER_WORDS = (
    "TRANSFER",
    "TFR",
    "SAVINGS",
    "SAVER",
    "POT",
    "ISA",
    "OWN ACCOUNT",
    "INTERNAL",
    "PAYMENT RECEIVED",
    "THANK YOU",
    "CARD REPAYMENT",
    "MOBILE PAYMENT",
)
ONE_SIDED = "transfer_one_sided"  # evidence.kind of a transfer whose other half is missing
AGAINST_WORDS = ("REFUND", "REVERSAL", "CASHBACK")  # money back from a shop isn't a transfer
# A pair at or above this is "inferred" (Sorted); below it, a guess the person can see as one.
# The Categoriser's own review threshold (`review_below`) by default.
SURE = 0.8
# Equal amounts with no word saying "transfer" (and not naming the other account) are often a
# coincidence: a payment to a friend and a shop refund on a card that doesn't print REFUND,
# or two unrelated payments on the same day. Real repayments and transfers almost always say
# so ("PAYMENT RECEIVED", "THANK YOU", the provider or the account's name), so these stay
# guesses.
NO_WORDS = 0.75
NO_WORDS_DAYS_APART = 0.7


@dataclass(frozen=True)
class Movement:
    id: str
    account_id: str
    account_kind: str
    date: date
    amount_pence: int
    text: str  # upper case


@dataclass(frozen=True)
class Pair:
    out_id: str
    in_id: str
    days_apart: int
    card_repayment: bool
    words: tuple[str, ...]
    against: bool = False

    @property
    def confidence(self) -> float:
        if self.against:
            return 0.6
        if self.words:
            return 0.99
        if self.card_repayment or self.days_apart == 0:
            return NO_WORDS
        return NO_WORDS_DAYS_APART


def _words(text: str, extra: Sequence[str]) -> list[str]:
    found = [w for w in TRANSFER_WORDS if re.search(rf"\b{re.escape(w)}\b", text)]
    found += [
        w for w in extra if w and re.search(rf"(?<![A-Z0-9]){re.escape(w)}(?![A-Z0-9])", text)
    ]
    return found


def pair_transfers(
    movements: Sequence[Movement],
    *,
    scope: set[str],
    max_days_apart: int = 3,
    names: Mapping[str, Sequence[str]] | None = None,
) -> list[Pair]:
    """Pair each movement with at most one opposite movement on another account.

    `names[account_id]` lists words that identify that account in a description (its
    nickname, its provider's name, its last 4 digits)."""
    names = names or {}
    by_amount: dict[int, list[Movement]] = {}
    for m in movements:
        if m.amount_pence > 0:
            by_amount.setdefault(m.amount_pence, []).append(m)
    ranked: list[tuple[tuple[Any, ...], Pair]] = []
    for out in (m for m in movements if m.amount_pence < 0):
        for arrival in by_amount.get(-out.amount_pence, []):
            if arrival.account_id == out.account_id:
                continue
            if out.id not in scope and arrival.id not in scope:
                continue
            gap = abs((arrival.date - out.date).days)
            if gap > max_days_apart:
                continue
            words = _words(out.text, names.get(arrival.account_id, ())) + _words(
                arrival.text, names.get(out.account_id, ())
            )
            card = arrival.account_kind == "credit_card" and out.account_kind != "credit_card"
            against = any(
                re.search(rf"\b{w}\b", t) for w in AGAINST_WORDS for t in (out.text, arrival.text)
            )
            pair = Pair(out.id, arrival.id, gap, card, tuple(dict.fromkeys(words)), against)
            ranked.append(((gap, -len(pair.words), out.date, out.id, arrival.id), pair))
    ranked.sort(key=lambda item: item[0])
    used: set[str] = set()
    pairs: list[Pair] = []
    for _, pair in ranked:
        if pair.out_id in used or pair.in_id in used:
            continue
        used.update((pair.out_id, pair.in_id))
        pairs.append(pair)
    return pairs


def covered(periods: Sequence[tuple[date, date]], day: date, slack: int) -> bool:
    return any(
        start - timedelta(days=slack) <= day <= end + timedelta(days=slack)
        for start, end in periods
    )


class TransferMatcher:
    def __init__(
        self,
        db: Database,
        understanding: UnderstandingStore,
        versions: KnowledgeVersions,
        manifest: Callable[[], AgentManifest],
    ) -> None:
        self.db, self.understanding, self.versions, self.manifest = (
            db,
            understanding,
            versions,
            manifest,
        )

    def _accounts(self, conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        accounts = {
            r["id"]: dict(r)
            for r in conn.execute("SELECT id, kind, nickname, provider_name, last4 FROM account")
        }
        for account in accounts.values():
            words = [
                w.upper()
                for w in (account["nickname"], account["provider_name"])
                if w and len(w) >= 4 and w.lower() != "other"
            ]
            if account["last4"]:
                words.append(account["last4"])
            account["words"] = words
            account["periods"] = []
        for r in conn.execute(
            "SELECT account_id, period_start, period_end FROM statement"
            " WHERE status = 'imported' AND period_start IS NOT NULL AND period_end IS NOT NULL"
        ):
            if r["account_id"] in accounts:
                accounts[r["account_id"]]["periods"].append(
                    (date.fromisoformat(r["period_start"]), date.fromisoformat(r["period_end"]))
                )
        return accounts

    def run(self, scope_ids: Sequence[str], *, run_id: str) -> dict[str, int]:
        days = int(self.manifest().limits.get("max_days_apart", 3))
        counts = {"pairs": 0, "one_sided": 0, "released": 0}
        with self.db.transaction() as conn:
            version = self.versions.current_in(conn)
            orphans = [
                r[0]
                for r in conn.execute(
                    "SELECT transaction_id FROM understanding WHERE transfer_pair_id IS NULL"
                    " AND status != 'confirmed'"
                    " AND json_extract(evidence, '$.kind') = 'transfer_pair'"
                )
            ]
            for txn_id in orphans:
                counts["released"] += self.understanding.release(
                    conn, txn_id, actor=NAME, reason="the other side of this transfer was removed"
                )
            scope = set(scope_ids)
            if not scope:
                return counts
            span = conn.execute(
                'SELECT MIN(date), MAX(date) FROM "transaction"'
                " WHERE id IN (SELECT value FROM json_each(?))",
                [json.dumps(sorted(scope))],
            ).fetchone()
            if span[0] is None:
                return counts
            lo = date.fromisoformat(span[0]) - timedelta(days=days)
            hi = date.fromisoformat(span[1]) + timedelta(days=days)
            accounts = self._accounts(conn)
            movements = [
                Movement(
                    r["id"],
                    r["account_id"],
                    accounts[r["account_id"]]["kind"],
                    date.fromisoformat(r["date"]),
                    r["amount_pence"],
                    " ".join(f"{r['raw_description']} {r['merchant_text'] or ''}".upper().split()),
                )
                for r in conn.execute(
                    "SELECT t.id, t.account_id, t.date, t.amount_pence, t.raw_description,"
                    ' t.merchant_text FROM "transaction" t'
                    " JOIN understanding u ON u.transaction_id = t.id"
                    " WHERE t.date BETWEEN ? AND ? AND u.status != 'confirmed'"
                    " AND u.ignored = 0 AND u.transfer_pair_id IS NULL"
                    " AND (u.authority < ? OR json_extract(u.evidence, '$.kind') = ?)",
                    [lo.isoformat(), hi.isoformat(), CODE_RULE, ONE_SIDED],
                )
            ]
            names = {a: tuple(v["words"]) for a, v in accounts.items()}
            pairs = pair_transfers(movements, scope=scope, max_days_apart=days, names=names)
            paired: set[str] = set()
            for pair in pairs:
                category = CARD_REPAYMENT if pair.card_repayment else BETWEEN_ACCOUNTS
                confidence = pair.confidence
                for this, other in ((pair.out_id, pair.in_id), (pair.in_id, pair.out_id)):
                    decision = Decision(
                        decided_by="rule",
                        authority=CODE_RULE,
                        status="inferred" if confidence >= SURE else "guessed",
                        confidence=confidence,
                        category_id=category,
                        who=HOUSEHOLD,
                        is_transfer=True,
                        transfer_pair_id=other,
                        evidence={
                            "kind": "transfer_pair",
                            "pair": other,
                            "days_apart": pair.days_apart,
                            "words": list(pair.words),
                        },
                    )
                    self.understanding.apply(
                        conn,
                        this,
                        decision,
                        actor=NAME,
                        run_id=run_id,
                        knowledge_version=version,
                        reason="money moving between your accounts",
                    )
                paired.update((pair.out_id, pair.in_id))
                counts["pairs"] += 1
            for m in movements:
                if m.id not in scope or m.id in paired:
                    continue
                target = self._named_account(m, accounts, days)
                if target is None:
                    continue
                card = accounts[target]["kind"] == "credit_card" and m.amount_pence < 0
                decision = Decision(
                    decided_by="rule",
                    authority=CODE_RULE,
                    status="inferred",
                    confidence=0.85,
                    category_id=CARD_REPAYMENT if card else BETWEEN_ACCOUNTS,
                    who=HOUSEHOLD,
                    is_transfer=True,
                    evidence={"kind": ONE_SIDED, "account": target},
                )
                counts["one_sided"] += self.understanding.apply(
                    conn,
                    m.id,
                    decision,
                    actor=NAME,
                    run_id=run_id,
                    knowledge_version=version,
                    reason="names another of your accounts",
                )
        return counts

    @staticmethod
    def _named_account(
        m: Movement, accounts: Mapping[str, dict[str, Any]], days: int
    ) -> str | None:
        """Another account this description names, when its statement for that date isn't
        in Tuppence (otherwise the two-sided match would have found the other half)."""
        if not _words(m.text, ()):
            return None
        for account_id, account in accounts.items():
            if account_id == m.account_id or covered(account["periods"], m.date, days):
                continue
            if _words(m.text, account["words"]) != _words(m.text, ()):
                return account_id
        return None
