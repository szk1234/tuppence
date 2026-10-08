"""The Commitments specialist (spec §8.2): bills, subscriptions and instalments.

Code finds every regular series of payments across all history (`knowledge.cadence`),
works out the next due date, the yearly cost, price rises, missed and lapsed payments,
duplicate services and free trials that turned into paid plans. The AI only labels what
kind of commitment a merchant is, in batches, and only when the category and the bank's
own payment type don't already say; without a model those stay unlabelled.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from tuppence.agents.runtime import LayeredBudget, StructuredLLM
from tuppence.config.models import AgentManifest
from tuppence.core.db import Database
from tuppence.core.errors import safe_error_text
from tuppence.core.money import format_pounds
from tuppence.core.secrets import SecretError
from tuppence.ingest.prompts import load_prompt
from tuppence.ingest.sensitive import scrub
from tuppence.knowledge.cadence import (
    MIN_OCCURRENCES,
    Cadence,
    Payment,
    Series,
    annual_cost,
    detect,
    missed_since,
    next_due,
    status_of,
)
from tuppence.knowledge.commitments import CommitmentStore, Detected
from tuppence.knowledge.merchants import MerchantStore
from tuppence.knowledge.models import BusinessType
from tuppence.knowledge.rules import phrase_in
from tuppence.llm.context import (
    ContextBudget,
    max_tokens_for,
    plan_batches,
    structured_overhead,
    text_tokens,
)
from tuppence.llm.types import (
    BudgetExceeded,
    ContextTooLarge,
    LLMBadResponse,
    LLMError,
    Message,
)

NAME = "commitments"
OUT_PER_PAYMENT = 15  # reply tokens for one payment
# The client repairs a bad reply by sending the conversation again with that reply; batches
# keep room for it (see the Categoriser).
REPAIR_FIXED = 260
MERCHANT_CHARS = 80
# What kind of commitment a category usually is; the longest matching prefix wins.
KIND_BY_CATEGORY: dict[str, BusinessType] = {
    "housing": "bill",
    "housing.furnishing": "none",
    "transport.car.finance": "instalment",
    "transport.car.insurance": "bill",
    "transport.car.road-tax": "bill",
    "transport.car.breakdown": "bill",
    "transport.car.fuel": "none",
    "transport.car.parking": "none",
    "transport.car.servicing": "none",
    "transport.public": "none",
    "transport.taxi": "none",
    "food": "none",
    "children.childcare": "bill",
    "children.school": "bill",
    "children.activities": "subscription",
    "children.clothes": "none",
    "children.toys": "none",
    "health.fitness": "subscription",
    "health.insurance": "bill",
    "health.pharmacy": "none",
    "personal-care": "none",
    "clothing": "none",
    "entertainment.going-out": "none",
    "subscriptions": "subscription",
    "subscriptions.mobile": "bill",
    "holidays": "none",
    "gifts": "none",
    "charity": "subscription",
    "pets.insurance": "bill",
    "pets.food": "none",
    "education.courses": "subscription",
    "financial.loan-repayments": "instalment",
    "financial.protection": "bill",
    "financial.bank-fees": "bill",
    "financial.interest": "none",
    "income": "none",
    "savings": "none",
    "transfers": "none",
}
DUPLICATE_PARENTS = (
    "subscriptions.tv-streaming",
    "subscriptions.music",
    "subscriptions.software",
    "subscriptions.news",
)
COUNCIL_TAX = "housing.council-tax"
BILL_TYPES = ("DIRECT DEBIT", "STANDING ORDER", "DD", "SO")


def kind_for_category(category_id: str | None) -> BusinessType | None:
    if not category_id:
        return None
    parts = category_id.split(".")
    for n in range(len(parts), 0, -1):
        found = KIND_BY_CATEGORY.get(".".join(parts[:n]))
        if found is not None:
            return found
    return None


class LabelItem(BaseModel):
    ref: str
    kind: str


class LabelOut(BaseModel):
    payments: list[LabelItem]


@dataclass
class Group:
    merchant_id: str
    merchant_name: str
    account_id: str
    business_type: BusinessType | None
    business_type_source: str | None
    payments: list[Payment]
    categories: Counter[str]
    bank_types: set[str]

    @property
    def category_id(self) -> str | None:
        return self.categories.most_common(1)[0][0] if self.categories else None


@dataclass
class Labelling:
    """What asking the model for kinds came to."""

    kinds: dict[str, str] = field(default_factory=dict)  # merchant id → kind
    deferred: int = 0  # merchants left for the next run (budget, or a reply that stayed bad)
    awaiting_ai: int = 0  # merchants left because the AI couldn't be used
    bad_replies: int = 0
    scrub_failures: int = 0
    ai_problem: str = ""  # safe_error_text of the first AI problem, for the run summary
    stopped: str = ""

    def fail(self, exc: Exception, merchants: Sequence[str]) -> None:
        self.awaiting_ai += len(merchants)
        self.ai_problem = self.ai_problem or safe_error_text(exc)
        self.stopped = "awaiting_ai"

    def scrub_failed(self) -> None:
        self.scrub_failures += 1


@dataclass
class CommitmentsDeps:
    db: Database
    merchants: MerchantStore
    store: CommitmentStore
    llm: StructuredLLM
    context_window: Callable[[str], int]
    manifest: Callable[[], AgentManifest]
    prompts_dir: Path | None = None


def _min_occurrences(manifest: AgentManifest) -> dict[Cadence, int]:
    floor = int(manifest.limits.get("min_occurrences", 2))
    return {
        c: max(floor, int(manifest.limits.get(f"min_{c}", n))) for c, n in MIN_OCCURRENCES.items()
    }


class Commitments:
    def __init__(self, deps: CommitmentsDeps) -> None:
        self.d = deps

    def _groups(self, conn: sqlite3.Connection) -> list[Group]:
        groups: dict[tuple[str, str], Group] = {}
        for r in conn.execute(
            "SELECT t.id, t.date, t.amount_pence, t.account_id, t.bank_type, u.merchant_id,"
            " u.category_id, m.name, m.business_type, m.business_type_source"
            ' FROM "transaction" t JOIN understanding u ON u.transaction_id = t.id'
            " JOIN merchant m ON m.id = u.merchant_id"
            " WHERE t.amount_pence < 0 AND u.is_transfer = 0 AND u.ignored = 0"
            " ORDER BY t.date, t.id"
        ):
            key = (r["merchant_id"], r["account_id"])
            group = groups.get(key)
            if group is None:
                group = groups[key] = Group(
                    r["merchant_id"],
                    r["name"],
                    r["account_id"],
                    r["business_type"],
                    r["business_type_source"],
                    [],
                    Counter(),
                    set(),
                )
            group.payments.append(
                Payment(r["id"], date.fromisoformat(r["date"]), -r["amount_pence"])
            )
            if r["category_id"]:
                group.categories[r["category_id"]] += 1
            if r["bank_type"]:
                group.bank_types.add(r["bank_type"].upper())
        return list(groups.values())

    @staticmethod
    def _coverage(conn: sqlite3.Connection) -> dict[str, date]:
        """How far each account's history reaches: its latest statement's end, else its latest
        transaction. Lapses are judged against this, never against today's date."""
        out = {
            r[0]: date.fromisoformat(r[1])
            for r in conn.execute(
                'SELECT account_id, MAX(date) FROM "transaction" GROUP BY account_id'
            )
        }
        for r in conn.execute(
            "SELECT account_id, MAX(period_end) FROM statement WHERE status = 'imported'"
            " AND period_end IS NOT NULL GROUP BY account_id"
        ):
            if r[0] in out:
                out[r[0]] = max(out[r[0]], date.fromisoformat(r[1]))
        return out

    def _kind(self, group: Group) -> tuple[BusinessType | None, str]:
        if group.business_type is not None and group.business_type_source in ("user", "llm"):
            return group.business_type, group.business_type_source or "llm"
        by_category = kind_for_category(group.category_id)
        if by_category is not None:
            return by_category, "code"
        if any(phrase_in(b, t) for t in group.bank_types for b in BILL_TYPES):
            return "bill", "code"  # the bank says it's a direct debit or standing order
        return None, "code"

    def _label(
        self, unlabelled: Sequence[tuple[Group, Series]], run_id: str, budget: LayeredBudget | None
    ) -> Labelling:
        """Ask the model what kind each merchant is (merchant id → kind). An AI problem never
        fails the run (G5): the budget stops it and defers the rest, a reply that is still bad
        after the client's repair defers that batch, and any other problem leaves the rest
        awaiting AI with the reason kept."""
        result = Labelling()
        if not unlabelled:
            return result
        names = list(dict.fromkeys(g.merchant_id for g, _ in unlabelled))
        try:
            context = ContextBudget(self.d.context_window("categorise"))
        except (LLMError, SecretError) as exc:
            result.fail(exc, names)
            return result
        prompt = load_prompt("commitment_labels", self.d.prompts_dir)

        def clean(text: str) -> str:
            # the whole text first, then the cut: a cut can leave part of a number
            return scrub(text, failed=result.scrub_failed)[:MERCHANT_CHARS]

        lines: dict[str, str] = {}
        for g, s in unlabelled:
            lines.setdefault(
                g.merchant_id,
                json.dumps(
                    {
                        "ref": "P000",
                        "merchant": clean(g.merchant_name),
                        "category_id": g.category_id,
                        "every": s.cadence,
                        "amount": format_pounds(s.expected_amount_pence),
                    }
                ),
            )
        fixed = structured_overhead(LabelOut) + text_tokens(prompt) + 20 + REPAIR_FIXED
        try:
            batches = plan_batches(
                names,
                budget=context,
                fixed_tokens=fixed,
                item_tokens=lambda m: text_tokens(lines[m]) + 1 + OUT_PER_PAYMENT,
                output_tokens_per_item=OUT_PER_PAYMENT,
                max_items=40,
            )
        except ContextTooLarge as exc:
            result.fail(exc, names)
            return result
        for index, batch in enumerate(batches):
            refs = {f"P{n}": m for n, m in enumerate(batch, start=1)}
            body = "\n".join(lines[m].replace('"P000"', f'"{ref}"') for ref, m in refs.items())
            try:
                reply: LabelOut = self.d.llm.structured(
                    "categorise",
                    [
                        Message(role="system", content=prompt),
                        Message(role="user", content=f"PAYMENTS:\n{body}"),
                    ],
                    LabelOut,
                    max_tokens=max_tokens_for(len(batch), per_item=OUT_PER_PAYMENT, budget=context),
                    run=budget,
                    run_id=run_id,
                )
            except BudgetExceeded:
                result.deferred += sum(len(b) for b in batches[index:])
                result.stopped = "budget"
                break
            except LLMBadResponse:
                result.deferred += len(batch)
                result.bad_replies += 1
                continue
            except (LLMError, SecretError) as exc:
                result.fail(exc, [m for b in batches[index:] for m in b])
                break
            for item in reply.payments:
                if item.ref in refs and item.kind in ("bill", "subscription", "instalment", "none"):
                    result.kinds[refs[item.ref]] = item.kind
        return result

    def run(self, *, run_id: str, budget: LayeredBudget | None) -> dict[str, Any]:
        manifest = self.d.manifest()
        minimums = _min_occurrences(manifest)
        tolerance = float(manifest.thresholds.get("amount_tolerance", 0.15))
        with self.d.db.connection() as conn:
            groups = self._groups(conn)
            coverage = self._coverage(conn)
            dismissed = CommitmentStore.dismissed_pairs(conn)
        found: list[tuple[Group, Series, BusinessType | None, str]] = []
        for group in groups:
            if (group.merchant_id, group.account_id) in dismissed:
                continue
            as_of = coverage.get(group.account_id, group.payments[-1].date)
            gaps = frozenset({2, 3}) if group.category_id == COUNCIL_TAX else frozenset()
            for series in detect(
                group.payments,
                as_of=as_of,
                tolerance=tolerance,
                min_occurrences=minimums,
                expected_gaps=gaps,
            ):
                kind, source = self._kind(group)
                found.append((group, series, kind, source))
        labelling = self._label([(g, s) for g, s, k, _ in found if k is None], run_id, budget)
        labels = labelling.kinds
        with self.d.db.transaction() as conn:
            for merchant_id, kind in labels.items():
                self.d.merchants.set_business_type(conn, merchant_id, kind, "llm")  # type: ignore[arg-type]
            detected = self._detected(found, labels, coverage)
            counts: dict[str, Any] = dict(self.d.store.sync(conn, detected))
        counts["labelled"] = len(labels)
        counts["found"] = len(detected)
        counts["deferred"] = labelling.deferred
        counts["awaiting_ai"] = labelling.awaiting_ai
        counts["bad_replies"] = labelling.bad_replies
        counts["scrub_failures"] = labelling.scrub_failures
        if labelling.ai_problem:
            counts["ai_problem"] = labelling.ai_problem
        return counts

    def _detected(
        self,
        found: Sequence[tuple[Group, Series, BusinessType | None, str]],
        labels: dict[str, str],
        coverage: dict[str, date],
    ) -> list[Detected]:
        out: list[Detected] = []
        for group, series, kind, source in found:
            if kind is None and group.merchant_id in labels:
                kind, source = labels[group.merchant_id], "llm"  # type: ignore[assignment]
            if kind in (None, "none"):
                continue
            as_of = coverage.get(group.account_id, series.last.date)
            status = status_of(series, as_of)
            flags: list[str] = []
            steps = series.price_steps
            if (
                len(steps) >= 2
                and steps[-1].amount_pence > steps[-2].amount_pence
                and (as_of - steps[-1].since) <= timedelta(days=365)
            ):
                flags.append("price_rise")
            if status == "lapsed":
                flags.append("lapsed")
            elif series.missed or missed_since(series, as_of):
                flags.append("missed")
            if series.trial is not None:
                flags.append("free_trial_converted")
            if series.varies:
                flags.append("varies")
            payments = list(series.payments) + ([series.trial] if series.trial else [])
            out.append(
                Detected(
                    merchant_id=group.merchant_id,
                    account_id=group.account_id,
                    category_id=group.category_id,
                    name=group.merchant_name,
                    kind=kind,  # type: ignore[arg-type]
                    kind_source=source,  # type: ignore[arg-type]
                    cadence=series.cadence,
                    expected_amount_pence=series.expected_amount_pence,
                    expected_day=series.expected_day,
                    skip_months=list(series.skip_months),
                    first_date=series.first.date,
                    last_date=series.last.date,
                    next_due=next_due(series),
                    annual_cost_pence=annual_cost(series),
                    payment_ids=[p.transaction_id for p in payments],
                    price_history=[
                        {"since": s.since.isoformat(), "amount_pence": s.amount_pence}
                        for s in steps
                    ],
                    flags=flags,  # type: ignore[arg-type]
                    status=status,
                    evidence={
                        "missed": [d.isoformat() for d in series.missed],
                        "trial": None
                        if series.trial is None
                        else {
                            "date": series.trial.date.isoformat(),
                            "amount_pence": series.trial.amount_pence,
                        },
                        "as_of": as_of.isoformat(),
                    },
                )
            )
        self._flag_duplicates(out)
        return out

    @staticmethod
    def _flag_duplicates(found: list[Detected]) -> None:
        """Two active subscriptions to the same kind of service (two music services), or the
        same merchant paid from two accounts."""
        active = [d for d in found if d.status == "active"]
        by_service: dict[str, list[Detected]] = {}
        for d in active:
            service = next(
                (
                    p
                    for p in DUPLICATE_PARENTS
                    if d.category_id and (d.category_id + ".").startswith(p + ".")
                ),
                None,
            )
            if service is not None:
                by_service.setdefault(service, []).append(d)
        by_merchant: dict[str, list[Detected]] = {}
        for d in active:
            by_merchant.setdefault(d.merchant_id, []).append(d)
        for group in [*by_service.values(), *by_merchant.values()]:
            if len(group) < 2:
                continue
            for d in group:
                if "duplicate" not in d.flags:
                    d.flags.append("duplicate")  # type: ignore[arg-type]
                others = [o.name for o in group if o is not d]
                d.evidence["duplicate_of"] = sorted(
                    set(d.evidence.get("duplicate_of", [])) | set(others)
                )
