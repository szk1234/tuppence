"""The Categoriser (spec §8.2): rules and memory in code, then the AI in batches sized to
the model's context window, then a review pass for the rows the AI wasn't sure about.
When a category gets crowded it proposes sub-categories and re-files rows into them.

A fixed LangGraph subgraph: code → ask_model → review → refile → remember. The only AI
calls are bounded by the categoriser's manifest budget and the whole run's budget; when
either is reached the remaining rows are marked `deferred` for the next run. An AI problem
(no model, Local only, a notice to confirm, a key that can't be read, every model failing)
marks them `awaiting_ai` and keeps the reason for the run summary: it never fails the run.

What a prompt says about a transaction is scrubbed first (`sensitive.scrub`): account, sort
code, card and IBAN details and postcodes never reach a model. Confirmed rows are the
person's: they are never sent to a model and never written.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from tuppence.agents.runtime import AnalysisContext, StructuredLLM
from tuppence.config.models import AgentManifest
from tuppence.core.db import Database
from tuppence.core.errors import InputError, safe_error_text
from tuppence.core.money import format_pounds
from tuppence.core.secrets import SecretError
from tuppence.ingest.prompts import load_prompt
from tuppence.ingest.sensitive import scrub
from tuppence.knowledge.authority import CONFIRMED_MEMORY, INFERRED_MEMORY, MODEL
from tuppence.knowledge.categories import AGENT_MAX_LEVEL, CategoryStore, CategoryTree, slugify
from tuppence.knowledge.merchants import MerchantStore, infer_memory
from tuppence.knowledge.models import HOUSEHOLD, Decision, Status, Waiting
from tuppence.knowledge.refiles import RefileStore, refile_decision
from tuppence.knowledge.rules import RuleStore, best_rule, load_txn_facts, rule_decision
from tuppence.knowledge.understanding import UnderstandingStore
from tuppence.knowledge.versions import STALE_SQL, KnowledgeVersions
from tuppence.llm.context import (
    MESSAGE_TOKENS,
    ContextBudget,
    capacity,
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

NAME = "categoriser"
OUT_PER_ROW = 45  # reply tokens for one transaction
# M1b's structured() repairs a bad reply by sending the conversation again with that reply (up
# to its max_tokens, cut at 4,000 characters) and a "that wasn't valid" message, and the client
# refuses anything over 60% of the window. Batches are planned so the repair turn fits as well:
# each call keeps room for the reply's base allowance, the repair message and two message
# overheads, and each row pays for its share of the echoed reply.
REPAIR_FIXED = 260
REPAIR_PER_ROW = OUT_PER_ROW
TREE_DEPTHS = (AGENT_MAX_LEVEL, 3, 2, 1)  # small models get a shallower tree (spec §10.3)
MIN_ROWS = 3  # a tree depth must leave room for at least this many rows per call
REFILE_MAX_TOKENS = 600
REFILE_MIN_MERCHANTS = 4  # a category is crowded with at least this many merchants
DESCRIPTION_CHARS = 120
BANK_CATEGORY_CHARS = 40
COUNT_KEYS = (
    "scope",
    "rule",
    "memory",
    "llm",
    "review",
    "deferred",
    "awaiting_ai",
    "unanswered",
    "bad_replies",
    "refiled",
    "new_categories",
    "memory_updates",
)

Stopped = Literal["", "budget", "awaiting_ai"]


class CategoriseItem(BaseModel):
    ref: str
    category_id: str
    who: str
    confidence: float
    reason: str


class CategoriseOut(BaseModel):
    transactions: list[CategoriseItem]


class RefileGroup(BaseModel):
    label: str
    merchants: list[str]


class RefileOut(BaseModel):
    subcategories: list[RefileGroup]


@dataclass(frozen=True)
class PersonRef:
    id: str
    name: str
    role: str


@dataclass
class CategoriserDeps:
    db: Database
    versions: KnowledgeVersions
    understanding: UnderstandingStore
    categories: CategoryStore
    merchants: MerchantStore
    rules: RuleStore
    refiles: RefileStore
    llm: StructuredLLM
    # task → the context window to plan for. May raise any AI problem (G5): no model, every
    # model blocked by Local only, a key that can't be read.
    context_window: Callable[[str], int]
    people: Callable[[], list[PersonRef]]
    manifest: Callable[[], AgentManifest]
    prompts_dir: Path | None = None
    today: Callable[[], date] = date.today


class CategoriserIn(TypedDict):
    run_id: str
    scope_ids: list[str]


class CategoriserOut(TypedDict):
    categoriser: dict[str, Any]


class CategoriserState(TypedDict, total=False):
    run_id: str
    scope_ids: list[str]
    pending: list[str]  # rows for the model
    low: list[str]  # rows the model wasn't sure about: for review
    merchants: list[str]  # merchants seen in this run
    counts: dict[str, int]
    stopped: Stopped
    ai_problem: str  # why the AI couldn't be used (safe_error_text), for the run summary
    categoriser: dict[str, Any]


@dataclass(frozen=True)
class RowInfo:
    id: str
    date: str
    amount_pence: int
    raw_description: str
    merchant_text: str | None
    bank_category: str | None
    account_kind: str
    owner_ids: tuple[str, ...]
    merchant_id: str | None
    merchant_name: str | None
    memory_category: str | None
    memory_seen: int


def _rows(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, RowInfo]:
    owners: dict[str, list[str]] = {}
    for r in conn.execute("SELECT account_id, person_id FROM account_owner ORDER BY person_id"):
        owners.setdefault(r["account_id"], []).append(r["person_id"])
    out: dict[str, RowInfo] = {}
    for r in conn.execute(
        "SELECT t.id, t.date, t.amount_pence, t.raw_description, t.merchant_text,"
        " t.bank_category, t.account_id, a.kind AS account_kind, u.merchant_id,"
        " m.name AS merchant_name, m.default_category_id, m.seen_count"
        ' FROM "transaction" t JOIN account a ON a.id = t.account_id'
        " JOIN understanding u ON u.transaction_id = t.id"
        " LEFT JOIN merchant m ON m.id = u.merchant_id"
        " WHERE t.id IN (SELECT value FROM json_each(?))",
        [json.dumps(list(ids))],
    ):
        out[r["id"]] = RowInfo(
            id=r["id"],
            date=r["date"],
            amount_pence=r["amount_pence"],
            raw_description=r["raw_description"],
            merchant_text=r["merchant_text"],
            bank_category=r["bank_category"],
            account_kind=r["account_kind"],
            owner_ids=tuple(owners.get(r["account_id"], [])),
            merchant_id=r["merchant_id"],
            merchant_name=r["merchant_name"],
            memory_category=r["default_category_id"],
            memory_seen=r["seen_count"] or 0,
        )
    return out


def default_who(owner_ids: Sequence[str]) -> str:
    """One account holder: them. A joint account (or none recorded): the household."""
    return owner_ids[0] if len(owner_ids) == 1 else HOUSEHOLD


def _status_for(confidence: float, review_below: float) -> Status:
    return "inferred" if confidence >= review_below else "guessed"


def _zero_counts() -> dict[str, int]:
    return dict.fromkeys(COUNT_KEYS, 0)


class _Progress:
    """A node's view of the run so far: the counts, why it stopped, and how AI failures are
    handled (G5). A failure never escapes: the rows it leaves wait for the next run."""

    def __init__(self, state: CategoriserState, mark: Callable[[list[str], Waiting], None]):
        self.counts: dict[str, int] = {**_zero_counts(), **state.get("counts", {})}
        self.stopped: Stopped = state.get("stopped", "")
        self.ai_problem = state.get("ai_problem", "")
        self._mark = mark

    def wait(self, ids: Sequence[str], waiting: Literal["deferred", "awaiting_ai"]) -> None:
        if ids:
            self._mark(list(ids), waiting)
            self.counts[waiting] += len(ids)

    def failed(self, exc: Exception, *, batch: Sequence[str], rest: Sequence[str]) -> bool:
        """Handle an AI failure in this order: the budget stops the Categoriser and defers
        `rest`; a reply that still isn't valid after the client's repair defers `batch` and
        the run carries on; any other AI problem (LLMError or SecretError: no model, Local
        only, a notice, a key, every model failing, a model too small) leaves `rest` awaiting
        AI with the reason kept. True when the node should stop."""
        if isinstance(exc, BudgetExceeded):
            self.wait(rest, "deferred")
            self.stopped = "budget"
            return True
        if isinstance(exc, LLMBadResponse):
            self.wait(batch, "deferred")
            self.counts["bad_replies"] += 1
            return False
        self.wait(rest, "awaiting_ai")
        self.stopped = "awaiting_ai"
        self.ai_problem = self.ai_problem or safe_error_text(exc)
        return True

    def update(self) -> dict[str, Any]:
        return {"counts": self.counts, "stopped": self.stopped, "ai_problem": self.ai_problem}


class Categoriser:
    def __init__(self, deps: CategoriserDeps) -> None:
        self.d = deps

    # --- helpers -------------------------------------------------------------------------

    def _limit(self, key: str, default: int) -> int:
        return int(self.d.manifest().limits.get(key, default))

    def _threshold(self, key: str, default: float) -> float:
        return float(self.d.manifest().thresholds.get(key, default))

    def _people_block(self) -> tuple[str, dict[str, str]]:
        """The household's people. Names stay: the Pseudonymise setting governs them."""
        people = self.d.people()
        lines = [f"- {p.id}: {p.name} ({p.role})" for p in people]
        lines.append(f"- {HOUSEHOLD}: everyone in the household")
        return "PEOPLE:\n" + "\n".join(lines), {p.id: p.name for p in people}

    def _account_label(self, row: RowInfo, names: dict[str, str]) -> str:
        kind = {
            "current": "current account",
            "savings": "savings account",
            "credit_card": "credit card",
        }.get(row.account_kind, row.account_kind)
        if len(row.owner_ids) == 1:
            return f"{kind} ({names.get(row.owner_ids[0], 'one person')})"
        return f"{kind} (joint)"

    def _row_data(self, row: RowInfo, names: dict[str, str]) -> dict[str, Any]:
        """What a prompt says about a transaction. Statement text is scrubbed whole, then
        shortened (a cut first could leave part of an account number that no longer looks
        like one)."""
        data: dict[str, Any] = {
            "date": row.date,
            "amount": format_pounds(row.amount_pence),
            "description": scrub(row.raw_description)[:DESCRIPTION_CHARS],
            "merchant": scrub(row.merchant_name) if row.merchant_name else None,
            "account": self._account_label(row, names),
        }
        if row.bank_category:
            data["bank_category"] = scrub(row.bank_category)[:BANK_CATEGORY_CHARS]
        return data

    @staticmethod
    def _line(ref: str, data: dict[str, Any]) -> str:
        """One JSON object per line (the oracle reads each with json.loads)."""
        return json.dumps({"ref": ref, **data}, ensure_ascii=False)

    @staticmethod
    def _memory_line(row: RowInfo, tree: CategoryTree) -> str | None:
        if not row.merchant_name or not tree.usable(row.memory_category):
            return None
        name = scrub(row.merchant_name)
        return f"- {name}: {row.memory_category} (seen {row.memory_seen} times)"

    @staticmethod
    def _fixed(prompt: str, head: str, headings: str, schema: type[BaseModel]) -> int:
        """Tokens every call of a kind sends, apart from the tree and the rows."""
        return (
            structured_overhead(schema)
            + text_tokens(prompt)
            + text_tokens(head)
            + text_tokens(headings)
            + 2 * MESSAGE_TOKENS
        )

    def _choose_tree(
        self,
        tree: CategoryTree,
        *,
        budget: ContextBudget,
        fixed: int,
        sample_tokens: int,
        wanted: int,
    ) -> str:
        for depth in TREE_DEPTHS:
            text = tree.render(max_depth=depth)
            room = capacity(
                budget,
                fixed_tokens=fixed + REPAIR_FIXED + text_tokens(text),
                tokens_per_item=sample_tokens + REPAIR_PER_ROW,
                output_tokens_per_item=OUT_PER_ROW,
            )
            if room >= min(MIN_ROWS, wanted):
                return text
        raise ContextTooLarge(
            f"This AI model's context window ({budget.context_window:,} tokens) is too small to"
            " sort transactions. Choose a model with a bigger context window."
        )

    def _mark(self, ids: list[str], waiting: Waiting) -> None:
        with self.d.db.transaction() as conn:
            self.d.understanding.set_waiting(conn, ids, waiting)

    def _call(
        self,
        task: str,
        messages: list[Message],
        schema: type[Any],
        max_tokens: int,
        runtime: Runtime[AnalysisContext],
    ) -> Any:
        return self.d.llm.structured(
            task,
            messages,
            schema,
            max_tokens=max_tokens,
            run=runtime.context.budget(NAME),
            run_id=runtime.context.run_id,
        )

    # --- nodes ---------------------------------------------------------------------------

    def code(self, state: CategoriserState, runtime: Runtime[AnalysisContext]) -> dict[str, Any]:
        """Rules first, then confirmed memory, then clear inferred memory. No AI."""
        run_id = runtime.context.run_id
        ids = list(dict.fromkeys(state.get("scope_ids", [])))
        min_rows = self._limit("memory_min_rows", 3)
        min_conf = self._threshold("memory_min_confidence", 0.8)
        counts = _zero_counts()
        counts["scope"] = len(ids)
        pending: list[str] = []
        low: list[str] = []
        merchants: set[str] = set()
        hits: dict[str, int] = {}
        tree = self.d.categories.tree()
        with self.d.db.transaction() as conn:
            version = self.d.versions.current_in(conn)
            rules = [  # a rule whose category was retired since files nothing
                r
                for r in self.d.rules.active_in(conn)
                if r.set_category_id is None or tree.usable(r.set_category_id)
            ]
            raw = {
                r["id"]: r
                for r in conn.execute(
                    'SELECT id, raw_description, merchant_text FROM "transaction"'
                    " WHERE id IN (SELECT value FROM json_each(?))",
                    [json.dumps(ids)],
                )
            }
            for txn_id in ids:
                if txn_id not in raw:
                    continue
                merchant = self.d.merchants.resolve(
                    conn, raw[txn_id]["raw_description"], raw[txn_id]["merchant_text"]
                )
                if merchant is not None:
                    merchants.add(merchant.id)
                    conn.execute(  # never on a confirmed row: that is the person's
                        "UPDATE understanding SET merchant_id = ? WHERE transaction_id = ?"
                        " AND merchant_id IS NOT ? AND status != 'confirmed'",
                        [merchant.id, txn_id, merchant.id],
                    )
            for txn_id, row in self.d.understanding.many_in(conn, ids).items():
                if (
                    row.status not in ("confirmed", "unknown")
                    and row.category_id is not None
                    and not tree.usable(row.category_id)
                ):  # its category was retired: whoever decided it, it is filed again
                    self.d.understanding.release(
                        conn, txn_id, actor=NAME, reason="its category was retired", run_id=run_id
                    )
            facts = {f.id: f for f in load_txn_facts(conn, ids)}
            current = self.d.understanding.many_in(conn, ids)
            stale = {
                r[0]
                for r in conn.execute(
                    "SELECT u.transaction_id FROM understanding u"  # noqa: S608 - constant SQL
                    " WHERE u.transaction_id IN (SELECT value FROM json_each(?)) AND " + STALE_SQL,
                    [json.dumps(ids)],
                )
            }
            all_merchants = self.d.merchants.many_in(
                conn, [f.merchant_id for f in facts.values() if f.merchant_id]
            )
            for txn_id, f in facts.items():
                row = current[txn_id]
                if row.status == "confirmed":
                    continue  # the person's: never sent to a model, never written
                decision: Decision | None = None
                rule = best_rule(rules, f)
                merchant = all_merchants.get(f.merchant_id or "")
                if rule is not None:
                    decision = rule_decision(rule, category_kind=tree.kind_of(rule.set_category_id))
                    if decision.who is None and decision.is_transfer is not True:
                        decision.who = default_who(sorted(f.owner_ids))
                elif (
                    merchant is not None
                    and merchant.default_category_id
                    and tree.usable(merchant.default_category_id)
                    and (
                        merchant.memory == "confirmed"
                        or (
                            merchant.memory == "inferred"
                            and merchant.seen_count >= min_rows
                            and merchant.confidence >= min_conf
                        )
                    )
                ):
                    confirmed = merchant.memory == "confirmed"
                    kind = tree.kind_of(merchant.default_category_id)
                    decision = Decision(
                        decided_by="memory",
                        authority=CONFIRMED_MEMORY if confirmed else INFERRED_MEMORY,
                        status="inferred",
                        confidence=1.0 if confirmed else merchant.confidence,
                        category_id=merchant.default_category_id,
                        who=merchant.default_who or default_who(sorted(f.owner_ids)),
                        is_transfer=kind == "transfer",
                        evidence={
                            "memory": merchant.memory,
                            "merchant": merchant.name,
                            "seen": merchant.seen_count,
                        },
                    )
                if decision is not None:
                    reason = decision.evidence.get("rule") or f"usually {decision.category_id}"
                    if self.d.understanding.apply(
                        conn,
                        txn_id,
                        decision,
                        actor=NAME,
                        knowledge_version=version,
                        run_id=run_id,
                        reason=str(reason),
                    ):
                        counts[decision.decided_by] += 1
                        if rule is not None:
                            hits[rule.id] = hits.get(rule.id, 0) + 1
                    continue
                if row.authority > MODEL:
                    continue
                if row.status == "unknown" or (
                    row.decided_by in ("llm", "review") and txn_id in stale
                ):
                    pending.append(txn_id)
                elif (
                    row.decided_by == "llm"
                    and row.status == "guessed"
                    and row.waiting in ("deferred", "awaiting_ai")
                ):  # its second look was cut short last time: it gets that look now
                    low.append(txn_id)
            # Everything else in scope has had its look: it leaves the queue.
            waiting = set(pending) | set(low)
            conn.execute(
                "UPDATE understanding SET waiting = NULL WHERE status != 'confirmed'"
                " AND transaction_id IN (SELECT value FROM json_each(?))",
                [json.dumps([i for i in ids if i not in waiting])],
            )
            self.d.rules.record_hits(conn, hits)
        cap = self._limit("max_rows_per_run", 2000)
        if len(pending) > cap:
            sizes = {f.id: abs(f.amount_pence) for f in facts.values()}
            pending.sort(key=lambda i: -sizes.get(i, 0))
            self._mark(pending[cap:], "deferred")
            counts["deferred"] += len(pending) - cap
            pending = pending[:cap]
        return {
            "pending": pending,
            "low": low,
            "merchants": sorted(merchants),
            "counts": counts,
            "stopped": "",
            "ai_problem": "",
        }

    def ask_model(
        self, state: CategoriserState, runtime: Runtime[AnalysisContext]
    ) -> dict[str, Any]:
        progress = _Progress(state, self._mark)
        pending = list(state.get("pending", []))
        low = list(state.get("low", []))
        if not pending:
            return {**progress.update(), "low": low}
        try:
            budget = ContextBudget(self.d.context_window("categorise"))
        except (LLMError, SecretError) as exc:
            progress.failed(exc, batch=pending, rest=pending)
            return {**progress.update(), "low": low}
        prompt = load_prompt("categorise", self.d.prompts_dir)
        people_block, names = self._people_block()
        tree = self.d.categories.tree()
        with self.d.db.connection() as conn:
            rows = _rows(conn, pending)
        pending = [i for i in pending if i in rows]
        data = {i: self._row_data(rows[i], names) for i in pending}
        lines = {i: self._line("T000", data[i]) for i in pending}
        memory_of = {i: self._memory_line(rows[i], tree) for i in pending}
        sample = max((text_tokens(v) + 1 for v in lines.values()), default=40)
        head = f"TODAY: {self.d.today().isoformat()}\n{people_block}\n"
        fixed = self._fixed(
            prompt, head, "CATEGORIES:\n\nMEMORY:\n- none\nTRANSACTIONS:\n", CategoriseOut
        )
        review_below = self._threshold("review_below", 0.8)
        version = self.d.versions.current()
        try:
            tree_text = self._choose_tree(
                tree, budget=budget, fixed=fixed, sample_tokens=sample, wanted=len(lines)
            )
            batches = plan_batches(
                pending,
                budget=budget,
                fixed_tokens=fixed + REPAIR_FIXED + text_tokens(tree_text),
                item_tokens=lambda i: text_tokens(lines[i]) + 1 + REPAIR_PER_ROW,
                output_tokens_per_item=OUT_PER_ROW,
                shared=lambda i: (
                    (rows[i].merchant_id or "", text_tokens(m) + 1) if (m := memory_of[i]) else None
                ),
                max_items=self._limit("max_rows_per_batch", 40),
            )
        except ContextTooLarge as exc:  # a model too small even for a few rows
            progress.failed(exc, batch=pending, rest=pending)
            return {**progress.update(), "low": low}
        for index, batch in enumerate(batches):
            refs = {f"T{n}": txn_id for n, txn_id in enumerate(batch, start=1)}
            memory = sorted({m for i in batch if (m := memory_of[i])})
            body = "\n".join(self._line(ref, data[i]) for ref, i in refs.items())
            user = (
                f"{head}CATEGORIES:\n{tree_text}\nMEMORY:\n"
                f"{chr(10).join(memory) if memory else '- none'}\nTRANSACTIONS:\n{body}"
            )
            try:
                out: CategoriseOut = self._call(
                    "categorise",
                    [Message(role="system", content=prompt), Message(role="user", content=user)],
                    CategoriseOut,
                    max_tokens_for(len(batch), per_item=OUT_PER_ROW, budget=budget),
                    runtime,
                )
            except (LLMError, SecretError) as exc:
                rest = [i for b in batches[index:] for i in b]
                if progress.failed(exc, batch=batch, rest=rest):
                    break
                continue
            answered, decided = self._apply_answers(
                out.transactions,
                refs,
                rows,
                tree,
                names,
                by="llm",
                review_below=review_below,
                version=version,
                run_id=runtime.context.run_id,
            )
            progress.counts["llm"] += len(decided)
            low.extend(i for i, conf in decided.items() if conf < review_below)
            missing = [i for i in batch if i not in answered]
            if missing:
                progress.wait(missing, "deferred")
                progress.counts["unanswered"] += len(missing)
        return {**progress.update(), "low": low}

    def _apply_answers(
        self,
        items: Sequence[CategoriseItem],
        refs: dict[str, str],
        rows: dict[str, RowInfo],
        tree: CategoryTree,
        names: dict[str, str],
        *,
        by: Literal["llm", "review"],
        review_below: float,
        version: int,
        run_id: str,
    ) -> tuple[set[str], dict[str, float]]:
        """Check each answer and write the usable ones. Returns (answered ids, {id: confidence}
        for rows that changed). Refs are batch-local, so a reply can only reach its own batch;
        an invented or repeated ref, or a category outside the tree, is ignored."""
        seen: set[str] = set()
        answered: set[str] = set()
        decided: dict[str, float] = {}
        with self.d.db.transaction() as conn:
            for item in items:
                txn_id = refs.get(item.ref.strip())
                if txn_id is None or txn_id in seen:
                    continue
                seen.add(txn_id)
                category = item.category_id.strip()
                kind = tree.kind_of(category)
                row = rows[txn_id]
                if not tree.usable(category) or (kind == "income" and row.amount_pence < 0):
                    continue
                answered.add(txn_id)
                who = (
                    item.who
                    if item.who in names or item.who == HOUSEHOLD
                    else default_who(row.owner_ids)
                )
                confidence = min(1.0, max(0.0, float(item.confidence)))
                decision = Decision(
                    decided_by=by,
                    authority=MODEL,
                    status=_status_for(confidence, review_below),
                    confidence=confidence,
                    category_id=category,
                    who=HOUSEHOLD if kind == "transfer" else who,
                    is_transfer=kind == "transfer",
                    evidence={"reason": item.reason[:200], "by": by},
                )
                if self.d.understanding.apply(
                    conn,
                    txn_id,
                    decision,
                    actor=NAME,
                    knowledge_version=version,
                    run_id=run_id,
                    reason=item.reason[:200],
                ):
                    decided[txn_id] = confidence
        return answered, decided

    def review(self, state: CategoriserState, runtime: Runtime[AnalysisContext]) -> dict[str, Any]:
        """A second look at the rows the model wasn't sure about (the `review` task). A row
        whose second look doesn't happen keeps its first answer as a guess and waits: the next
        run gives it that look."""
        progress = _Progress(state, self._mark)
        low = list(dict.fromkeys(state.get("low", [])))
        if not low:
            return progress.update()
        if progress.stopped:  # the run stopped before the second look
            progress.wait(low, "deferred" if progress.stopped == "budget" else "awaiting_ai")
            return progress.update()
        try:
            budget = ContextBudget(self.d.context_window("review"))
        except (LLMError, SecretError) as exc:
            progress.failed(exc, batch=low, rest=low)
            return progress.update()
        prompt = load_prompt("review", self.d.prompts_dir)
        people_block, names = self._people_block()
        tree = self.d.categories.tree()
        with self.d.db.connection() as conn:
            rows = _rows(conn, low)
            given = self.d.understanding.many_in(conn, low)
        low = [i for i in low if i in rows and i in given]
        review_below = self._threshold("review_below", 0.8)
        data = {
            i: {
                **self._row_data(rows[i], names),
                "given_category_id": given[i].category_id,
                "given_confidence": round(given[i].confidence, 2),
                "given_reason": scrub(str(given[i].evidence.get("reason", "")))[:80],
            }
            for i in low
        }
        lines = {i: self._line("T000", data[i]) for i in low}
        head = f"TODAY: {self.d.today().isoformat()}\n{people_block}\n"
        fixed = self._fixed(prompt, head, "CATEGORIES:\n\nTRANSACTIONS:\n", CategoriseOut)
        try:
            sample = max((text_tokens(v) + 1 for v in lines.values()), default=40)
            tree_text = self._choose_tree(
                tree, budget=budget, fixed=fixed, sample_tokens=sample, wanted=len(low)
            )
            batches = plan_batches(
                low,
                budget=budget,
                fixed_tokens=fixed + REPAIR_FIXED + text_tokens(tree_text),
                item_tokens=lambda i: text_tokens(lines[i]) + 1 + REPAIR_PER_ROW,
                output_tokens_per_item=OUT_PER_ROW,
                max_items=self._limit("max_rows_per_batch", 40),
            )
        except ContextTooLarge as exc:
            progress.failed(exc, batch=low, rest=low)
            return progress.update()
        version = self.d.versions.current()
        for index, batch in enumerate(batches):
            refs = {f"T{n}": txn_id for n, txn_id in enumerate(batch, start=1)}
            body = "\n".join(self._line(ref, data[i]) for ref, i in refs.items())
            user = f"{head}CATEGORIES:\n{tree_text}\nTRANSACTIONS:\n{body}"
            try:
                out: CategoriseOut = self._call(
                    "review",
                    [Message(role="system", content=prompt), Message(role="user", content=user)],
                    CategoriseOut,
                    max_tokens_for(len(batch), per_item=OUT_PER_ROW, budget=budget),
                    runtime,
                )
            except (LLMError, SecretError) as exc:
                rest = [i for b in batches[index:] for i in b]
                if progress.failed(exc, batch=batch, rest=rest):
                    break
                continue
            answered, decided = self._apply_answers(
                out.transactions,
                refs,
                rows,
                tree,
                names,
                by="review",
                review_below=review_below,
                version=version,
                run_id=runtime.context.run_id,
            )
            progress.counts["review"] += len(decided)
            missing = [i for i in batch if i not in answered]
            if missing:
                progress.wait(missing, "deferred")
                progress.counts["unanswered"] += len(missing)
        return progress.update()

    def refile(self, state: CategoriserState, runtime: Runtime[AnalysisContext]) -> dict[str, Any]:
        """Split one crowded category into sub-categories the model proposes (logged and
        undoable). At most `max_refiles_per_run` per run; each category is considered once
        (a split that didn't happen because of an AI problem is asked again next run)."""
        progress = _Progress(state, self._mark)
        if progress.stopped or self._limit("max_refiles_per_run", 1) < 1:
            return progress.update()
        crowded_rows = self._limit("crowded_category_rows", 40)
        tree = self.d.categories.tree()
        with self.d.db.connection() as conn:
            candidates = conn.execute(
                "SELECT u.category_id, COUNT(*) AS n, COUNT(DISTINCT u.merchant_id) AS merchants"
                " FROM understanding u JOIN category c ON c.id = u.category_id"
                " WHERE c.kind = 'spend' AND c.retired = 0 AND c.level < ?"
                " AND u.merchant_id IS NOT NULL GROUP BY u.category_id"
                " HAVING n >= ? AND merchants >= ? ORDER BY n DESC",
                [AGENT_MAX_LEVEL, crowded_rows, REFILE_MIN_MERCHANTS],
            ).fetchall()
            parent = next(
                (
                    r["category_id"]
                    for r in candidates
                    if not RefileStore.considered(conn, r["category_id"])
                ),
                None,
            )
            if parent is None:
                return progress.update()
            merchants = conn.execute(
                "SELECT m.id, m.name, COUNT(*) AS n, SUM(ABS(t.amount_pence)) AS pence"
                " FROM understanding u JOIN merchant m ON m.id = u.merchant_id"
                ' JOIN "transaction" t ON t.id = u.transaction_id'
                " WHERE u.category_id = ? GROUP BY m.id ORDER BY pence DESC, m.id LIMIT 60",
                [parent],
            ).fetchall()
        try:
            budget = ContextBudget(self.d.context_window("categorise"))
        except (LLMError, SecretError) as exc:
            progress.failed(exc, batch=[], rest=[])
            return progress.update()
        prompt = load_prompt("refile", self.d.prompts_dir)
        max_tokens = min(budget.output_tokens, REFILE_MAX_TOKENS)
        path = " › ".join(c.label for c in tree.path(parent))
        heading = f"CATEGORY: {parent} ({path})\nMERCHANTS:\n"
        room = budget.input_tokens - (  # the biggest merchants that fit, with the repair turn
            self._fixed(prompt, heading, "", RefileOut) + REPAIR_FIXED + max_tokens
        )
        lines: list[str] = []
        for n, r in enumerate(merchants, start=1):
            line = f"M{n}: {scrub(r['name'])} — {r['n']} payments, £{format_pounds(r['pence'])}"
            if text_tokens(line) + 1 > room:
                break
            room -= text_tokens(line) + 1
            lines.append(line)
        if len(lines) < REFILE_MIN_MERCHANTS:
            return progress.update()
        refs = {f"M{n}": merchants[n - 1]["id"] for n in range(1, len(lines) + 1)}
        user = heading + "\n".join(lines)
        try:
            out: RefileOut = self._call(
                "categorise",
                [Message(role="system", content=prompt), Message(role="user", content=user)],
                RefileOut,
                max_tokens,
                runtime,
            )
        except (LLMError, SecretError) as exc:
            progress.failed(exc, batch=[], rest=[])
            return progress.update()
        groups = self._valid_groups(out, refs, tree, parent)
        moved, created = self._apply_refile(parent, groups, runtime.context.run_id)
        progress.counts["refiled"] = moved
        progress.counts["new_categories"] = len(created)
        return progress.update()

    @staticmethod
    def _valid_groups(
        out: RefileOut, refs: dict[str, str], tree: CategoryTree, parent: str
    ) -> list[tuple[str, list[str]]]:
        groups: list[tuple[str, list[str]]] = []
        used: set[str] = set()
        slugs = {slugify(c.label) for c in tree.children(parent, include_retired=True)}
        slugs.add(slugify(tree.by_id[parent].label))
        for group in out.subcategories[:6]:
            label = " ".join(group.label.split())
            slug = slugify(label)
            if not 1 <= len(label) <= 40 or not slug or slug in slugs:
                continue
            ids = [refs[m] for m in group.merchants if m in refs and refs[m] not in used]
            if not ids:
                continue
            used.update(ids)
            slugs.add(slug)
            groups.append((label, ids))
        return groups if len(groups) >= 2 else []

    def _apply_refile(
        self, parent: str, groups: list[tuple[str, list[str]]], run_id: str
    ) -> tuple[int, list[str]]:
        created: list[str] = []
        moves: list[dict[str, str]] = []
        with self.d.db.transaction() as conn:
            for label, merchant_ids in groups:
                try:
                    child = self.d.categories.create_in(
                        conn, parent_id=parent, label=label, source="agent"
                    )
                except InputError:
                    continue
                created.append(child.id)
                version = self.d.versions.current_in(conn)
                ids = [
                    r[0]
                    for r in conn.execute(
                        "SELECT transaction_id FROM understanding WHERE category_id = ?"
                        " AND merchant_id IN (SELECT value FROM json_each(?))"
                        " AND decided_by IN ('llm', 'review', 'memory')",
                        [parent, json.dumps(merchant_ids)],
                    )
                ]
                for txn_id in ids:
                    current = self.d.understanding.get_in(conn, txn_id)
                    decision = refile_decision(current, child.id)
                    if self.d.understanding.apply(
                        conn,
                        txn_id,
                        decision,
                        actor=NAME,
                        knowledge_version=version,
                        run_id=run_id,
                        reason=f"filed under {label}",
                    ):
                        moves.append({"transaction_id": txn_id, "to": child.id})
                conn.execute(
                    "UPDATE merchant SET default_category_id = ?"
                    " WHERE id IN (SELECT value FROM json_each(?))"
                    " AND memory = 'inferred' AND default_category_id = ?",
                    [child.id, json.dumps(merchant_ids), parent],
                )
            self.d.refiles.record(
                conn, run_id=run_id, parent_id=parent, created_ids=created, moves=moves
            )
        return len(moves), created

    def remember(
        self, state: CategoriserState, runtime: Runtime[AnalysisContext]
    ) -> dict[str, Any]:
        """Update merchant memory from what the model and the person decided."""
        progress = _Progress(state, self._mark)
        min_rows = self._limit("memory_min_rows", 3)
        updated = 0
        with self.d.db.transaction() as conn:
            for merchant_id in state.get("merchants", []):
                rows = conn.execute(
                    "SELECT u.category_id, u.who, u.confidence, u.decided_by,"
                    " ABS(t.amount_pence) AS pence FROM understanding u"
                    ' JOIN "transaction" t ON t.id = u.transaction_id'
                    " WHERE u.merchant_id = ?",
                    [merchant_id],
                ).fetchall()
                decisions = [
                    (r["category_id"], r["who"], r["confidence"], r["pence"])
                    for r in rows
                    if r["decided_by"] in ("llm", "review", "human") and r["category_id"]
                ]
                guess = infer_memory(decisions, min_rows=min_rows)
                if guess is None:
                    continue
                if self.d.merchants.remember(conn, merchant_id, guess, seen_count=len(rows)):
                    self.d.versions.bump(
                        conn,
                        "merchant",
                        merchant_id=merchant_id,
                        note=f"usually {guess.category_id}",
                    )
                    updated += 1
        progress.counts["memory_updates"] = updated
        summary = {
            **progress.counts,
            "stopped": progress.stopped,
            "ai_problem": progress.ai_problem,
        }
        return {**progress.update(), "categoriser": summary}

    # --- wiring --------------------------------------------------------------------------

    def build(self) -> Any:
        graph = StateGraph(
            CategoriserState,
            input_schema=CategoriserIn,
            output_schema=CategoriserOut,
            context_schema=AnalysisContext,
        )
        graph.add_node("code", self.code)
        graph.add_node("ask_model", self.ask_model)
        graph.add_node("review", self.review)
        graph.add_node("refile", self.refile)
        graph.add_node("remember", self.remember)
        graph.add_edge(START, "code")
        graph.add_edge("code", "ask_model")
        graph.add_edge("ask_model", "review")
        graph.add_edge("review", "refile")
        graph.add_edge("refile", "remember")
        graph.add_edge("remember", END)
        return graph.compile()
