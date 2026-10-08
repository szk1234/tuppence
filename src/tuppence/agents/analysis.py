"""The analysis workflow, M4's part of it (spec §8.1, §8.3): a fixed LangGraph graph.

    START → prepare → categorise → match_transfers → sweep → commitments → report → END

Code decides every step. The Categoriser is a subgraph with its own bounded steps; the
other specialists are code (the Commitments specialist may ask the model for labels). One
`analysis` job runs at a time (an exclusive job kind), new triggers merge into the one
pending job, and every run is checkpointed so a restart carries on where it stopped.
Researcher, Question planner, Purpose analyst, Life-events and the report writer join
this graph in M5 and M6; `report` is where the report refresh will go.

An AI problem never fails a run (G5): the specialists leave the rows they couldn't do
`deferred` (a cap was reached) or `awaiting_ai` (no usable model) and the run ends
`partial`, with the reason in its summary. Only a bug or a database problem fails one.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Required, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context
from pydantic import BaseModel

from tuppence.agents import backlog
from tuppence.agents.categoriser import Categoriser
from tuppence.agents.commitments import Commitments
from tuppence.agents.runtime import AnalysisContext, LayeredBudget
from tuppence.agents.transfers import TransferMatcher
from tuppence.config.models import AgentManifest
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.ingest.handoff import ANALYSIS_JOB, ANALYSIS_SCOPE, enqueue_analysis, request_analysis
from tuppence.knowledge.versions import KnowledgeVersions
from tuppence.llm.budget import RunBudget

RECURSION_LIMIT = 40
LLM_SPECIALISTS = ("categoriser", "commitments")
# A correction kept while its statement is read again (`understanding_carry`) waits this
# long for the same transaction to come back (R-M4-2).
CARRY_DAYS = 90
INTERRUPTED = "Tuppence stopped during this run."
UNDO_SPLIT = "(you can undo this on the Spending page, under “Sub-categories Tuppence added”)"
WILL_RETRY = "Something went wrong during this run. Tuppence will try again."


class AnalysisState(TypedDict, total=False):
    run_id: Required[str]
    job_id: int
    statement_ids: list[str]
    triggers: list[str]
    scope_ids: list[str]
    categoriser: dict[str, Any]
    transfers: dict[str, Any]
    sweep: dict[str, Any]
    commitments: dict[str, Any]
    summary: str


class AnalysisRun(BaseModel):
    id: str
    job_id: int | None
    triggers: list[str]
    statement_ids: list[str]
    status: str
    knowledge_version_start: int
    knowledge_version_end: int | None
    counts: dict[str, Any]
    summary: str
    llm_calls: int
    tokens: int
    cost_gbp: float
    stopped_reason: str | None
    started_at: str
    finished_at: str | None


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def stopped_reason(counts: dict[str, Any]) -> str | None:
    """Why the run stopped short ("budget" or "awaiting_ai"), or None. The Categoriser's
    reason first: it decides about rows; Commitments only labels merchants."""
    for name in LLM_SPECIALISTS:
        stopped = counts.get(name, {}).get("stopped")
        if stopped:
            return str(stopped)
    return None


def summarise(counts: dict[str, Any]) -> str:
    """The run's "what changed", in plain English, for the Home page and later the coach.

    `counts["waiting"]`, when present, is the number of rows left waiting at the end of the
    run, each counted once (the specialists' own counts can overlap: a row sorted in one
    pass and left waiting for its second look is counted by both)."""
    c = counts.get("categoriser", {})
    m = counts.get("commitments", {})
    parts: list[str] = []
    sorted_n = sum(int(c.get(k, 0)) for k in ("rule", "memory", "llm"))
    if sorted_n:
        bits = [
            f"{c.get('rule', 0)} by your rules" if c.get("rule") else "",
            f"{c.get('memory', 0)} from what Tuppence already knew" if c.get("memory") else "",
            f"{c.get('llm', 0)} by the AI" if c.get("llm") else "",
        ]
        sorted_text = _n(sorted_n, "transaction", "transactions")
        parts.append(f"Sorted {sorted_text} ({', '.join(b for b in bits if b)}).")
    if c.get("review"):
        looked = _n(int(c["review"]), "transaction", "transactions")
        parts.append(f"Took a second look at {looked} the AI wasn't sure about.")
    if c.get("new_categories"):
        parts.append(
            f"Added {c['new_categories']} sub-categories and moved {c.get('refiled', 0)}"
            f" transactions into them {UNDO_SPLIT}."
        )
    pairs = int(counts.get("transfers", {}).get("pairs", 0))
    if pairs:
        parts.append(f"Matched {_n(pairs, 'transfer', 'transfers')} between your accounts.")
    if m.get("new"):
        found = _n(int(m["new"]), "new bill or subscription", "new bills or subscriptions")
        parts.append(f"Found {found}.")
    if m.get("new_price_rises"):
        parts.append(f"Spotted {_n(int(m['new_price_rises']), 'price rise', 'price rises')}.")
    left = counts.get("waiting")
    if left is None:  # a summary of the Categoriser's counts alone
        left = {"deferred": c.get("deferred", 0), "awaiting_ai": c.get("awaiting_ai", 0)}
    waiting = sum(int(v) for v in left.values())
    stopped = stopped_reason(counts)
    problem = str(c.get("ai_problem") or m.get("ai_problem") or "")
    if stopped == "awaiting_ai":
        if waiting:
            are = "is" if waiting == 1 else "are"
            text = f"{_n(waiting, 'transaction', 'transactions')} {are} waiting for an AI model."
        else:  # the rows were sorted; something else needed the AI (a split, a label)
            text = "The AI couldn't be used this time."
        parts.append(f"{text} {problem or 'Check Settings › AI.'}")
    elif stopped == "budget":
        rest = (
            f"{_n(waiting, 'transaction', 'transactions')} {'waits' if waiting == 1 else 'wait'}"
            if waiting
            else "the rest waits"
        )
        parts.append(f"Stopped at this run's AI budget; {rest} for the next run.")
    elif waiting:
        parts.append(f"{_n(waiting, 'transaction', 'transactions')} will be tried again next run.")
    labels = int(m.get("awaiting_ai", 0)) + int(m.get("deferred", 0))
    if labels == 1:
        parts.append("1 regular payment waits for the AI to say if it's a bill or subscription.")
    elif labels:
        parts.append(
            f"{labels} regular payments wait for the AI to say if they're bills or subscriptions."
        )
    hidden = int(c.get("scrub_failures", 0)) + int(m.get("scrub_failures", 0))
    if hidden:
        parts.append(
            f"{_n(hidden, 'text was', 'texts were')} sent as <HIDDEN>: Tuppence couldn't check"
            " them for account details."
        )
    return " ".join(parts) or "Nothing new to sort."


@dataclass
class AnalysisDeps:
    db: Database
    versions: KnowledgeVersions
    categoriser: Categoriser
    transfers: TransferMatcher
    commitments: Commitments
    manifest: Callable[[str], AgentManifest]
    clock: Callable[[], datetime] = utcnow


class AnalysisGraph:
    def __init__(self, deps: AnalysisDeps) -> None:
        self.d = deps

    def prepare(self, state: AnalysisState) -> dict[str, Any]:
        """The run's scope: every row the payload's statements cover (M3's link table), then
        the rows waiting in the backlog (queued, deferred, awaiting AI), biggest first."""
        cap = int(self.d.manifest("categoriser").limits.get("max_rows_per_run", 2000))
        statement_ids = list(state.get("statement_ids", []))
        with self.d.db.transaction() as conn:
            new = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT t.id, t.date FROM statement_transaction l"
                    ' JOIN "transaction" t ON t.id = l.transaction_id'
                    " WHERE l.statement_id IN (SELECT value FROM json_each(?))"
                    " ORDER BY t.date, t.id",
                    [json.dumps(statement_ids)],
                )
            ]
            scope = list(dict.fromkeys([*new, *backlog.queued(conn, limit=cap)]))
            conn.execute(
                "INSERT INTO analysis_run (id, job_id, triggers, statement_ids, status,"
                " knowledge_version_start, started_at) VALUES (?, ?, ?, ?, 'running', ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET status = 'running', finished_at = NULL",
                [
                    state["run_id"],
                    state.get("job_id"),
                    json.dumps(state.get("triggers", [])),
                    json.dumps(statement_ids),
                    self.d.versions.current_in(conn),
                    to_iso(self.d.clock()),
                ],
            )
        return {"scope_ids": scope}

    def match_transfers(
        self, state: AnalysisState, runtime: Runtime[AnalysisContext]
    ) -> dict[str, Any]:
        if not self.d.manifest("transfer_matcher").enabled:
            return {"transfers": {}}
        return {
            "transfers": self.d.transfers.run(
                state.get("scope_ids", []), run_id=runtime.context.run_id
            )
        }

    def sweep(self, state: AnalysisState) -> dict[str, Any]:
        manifest = self.d.manifest("backlog_sweep")
        if not manifest.enabled:
            return {"sweep": {}}
        with self.d.db.transaction() as conn:
            counts = backlog.sweep(
                conn,
                revisit_below=float(manifest.thresholds.get("revisit_below_confidence", 0.7)),
                cap=int(manifest.limits.get("max_items_per_run", 200)),
            )
        return {"sweep": counts}

    def commitments(
        self, state: AnalysisState, runtime: Runtime[AnalysisContext]
    ) -> dict[str, Any]:
        if not self.d.manifest("commitments").enabled:
            return {"commitments": {}}
        return {
            "commitments": self.d.commitments.run(
                run_id=runtime.context.run_id, budget=runtime.context.budget("commitments")
            )
        }

    def _carry_cutoff(self) -> str:
        """`understanding_carry.carried_at` is written by a trigger with SQLite's own clock
        (UTC, the same text format as `to_iso`): rows carried before this are dropped."""
        return to_iso(self.d.clock() - timedelta(days=CARRY_DAYS))

    def report(self, state: AnalysisState, runtime: Runtime[AnalysisContext]) -> dict[str, Any]:
        """M4 writes the run summary here; M6's report refresh joins this step."""
        counts: dict[str, Any] = {
            k: state.get(k, {}) for k in ("categoriser", "transfers", "sweep", "commitments")
        }
        with self.d.db.transaction() as conn:
            counts["waiting"] = waiting_rows(conn, ("deferred", "awaiting_ai"))
            dropped = conn.execute(
                "DELETE FROM understanding_carry WHERE carried_at < ?", [self._carry_cutoff()]
            ).rowcount
            counts["housekeeping"] = {"carries_dropped": dropped}
            summary = summarise(counts)
            stopped = stopped_reason(counts)
            calls, tokens, cost = usage(runtime.context)
            conn.execute(
                "UPDATE analysis_run SET status = ?, knowledge_version_end = ?, counts = ?,"
                " summary = ?, llm_calls = llm_calls + ?, tokens = tokens + ?,"
                " cost_gbp = cost_gbp + ?, stopped_reason = ?, finished_at = ? WHERE id = ?",
                [
                    "partial" if stopped else "done",
                    self.d.versions.current_in(conn),
                    json.dumps(counts),
                    summary,
                    calls,
                    tokens,
                    cost,
                    stopped,
                    to_iso(self.d.clock()),
                    state["run_id"],
                ],
            )
            conn.execute(
                "UPDATE statement SET analysis_state = 'done' WHERE id IN"
                " (SELECT value FROM json_each(?)) AND status = 'imported'",
                [json.dumps(state.get("statement_ids", []))],
            )
        return {"summary": summary}

    def build(self, checkpointer: BaseCheckpointSaver | None) -> Any:
        graph = StateGraph(AnalysisState, context_schema=AnalysisContext)
        graph.add_node("prepare", self.prepare)
        graph.add_node("categorise", self.d.categoriser.build())
        graph.add_node("match_transfers", self.match_transfers)
        graph.add_node("sweep", self.sweep)
        graph.add_node("commitments", self.commitments)
        graph.add_node("report", self.report)
        graph.add_edge(START, "prepare")
        graph.add_edge("prepare", "categorise")
        graph.add_edge("categorise", "match_transfers")
        graph.add_edge("match_transfers", "sweep")
        graph.add_edge("sweep", "commitments")
        graph.add_edge("commitments", "report")
        graph.add_edge("report", END)
        return graph.compile(checkpointer=checkpointer)


def waiting_rows(conn: sqlite3.Connection, kinds: tuple[str, ...] | None = None) -> dict[str, int]:
    """How many rows wait, by why ("queued", "deferred", "awaiting_ai"); each row once."""
    rows = conn.execute(
        "SELECT waiting, COUNT(*) FROM understanding WHERE waiting IS NOT NULL"
        " AND status != 'confirmed' GROUP BY waiting ORDER BY waiting"
    )
    return {r[0]: int(r[1]) for r in rows if kinds is None or r[0] in kinds}


def triggers(statement_ids: list[str], reasons: list[str]) -> list[str]:
    """Why a run happens: statements imported, the reasons merged into its job, or the
    schedule."""
    return (["statement_imported"] if statement_ids else []) + reasons or ["scheduled"]


def usage(context: AnalysisContext) -> tuple[int, int, float]:
    """(calls, tokens, £) the AI specialists used in this attempt of the run."""
    budgets = [context.budgets[n] for n in LLM_SPECIALISTS if n in context.budgets]
    return (
        sum(b.calls for b in budgets),
        sum(b.tokens for b in budgets),
        round(sum(b.gbp for b in budgets), 4),
    )


class AnalysisService:
    """The `analysis` job handler, run requests and status for the API."""

    def __init__(
        self,
        *,
        db: Database,
        graph: AnalysisGraph,
        checkpointer: Any,
        queue: Any,
        manifest: Callable[[str], AgentManifest],
        run_cap_gbp: Callable[[], float],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db, self.queue, self.checkpointer = db, queue, checkpointer
        self.manifest, self.run_cap_gbp = manifest, run_cap_gbp
        self.monotonic = monotonic  # the budgets' clock
        self.deps = graph.d
        self.graph = graph.build(checkpointer)

    @staticmethod
    def thread_id(job_id: int) -> str:
        return f"analysis:{job_id}"

    def context(self, run_id: str) -> AnalysisContext:
        """Each AI specialist's own caps (its manifest, its £ lowered to the per-run cap)
        inside the whole run's caps (the sum of theirs, and the `llm.run_cap_gbp` setting for
        money). The run's clock starts now; a specialist's own clock starts when its step
        first asks for its budget, so a slow Categoriser doesn't use up Commitments' time.
        Rebuilt for every attempt: budgets are never checkpointed."""
        cap = float(self.run_cap_gbp())
        manifests = {n: self.manifest(n) for n in LLM_SPECIALISTS}
        run = RunBudget(
            max_calls=sum(m.budgets.max_llm_calls for m in manifests.values()),
            max_tokens=sum(m.budgets.max_tokens for m in manifests.values()),
            max_gbp=cap,
            max_seconds=sum(m.budgets.max_seconds for m in manifests.values()),
            monotonic=self.monotonic,
        )

        def own(manifest: AgentManifest) -> Callable[[], LayeredBudget]:
            def make() -> LayeredBudget:
                budget = RunBudget.from_manifest(manifest.budgets, cap)
                return LayeredBudget(replace(budget, monotonic=self.monotonic, started=-1.0), run)

            return make

        return AnalysisContext(run_id=run_id, factories={n: own(m) for n, m in manifests.items()})

    def handle_job(self, job: Any) -> dict[str, Any]:
        """Run (or carry on) the household's analysis. A run a crash or a restart cut short
        carries on from its last checkpoint, without asking the AI again for what it did."""
        run_id = f"ar_{job.id}"
        config: dict[str, Any] = {
            "configurable": {"thread_id": self.thread_id(job.id)},
            "recursion_limit": RECURSION_LIMIT,
        }
        statement_ids = sorted(set(job.payload.get("statement_ids", [])))
        reasons = sorted(set(job.payload.get("reasons", [])))
        context: AnalysisContext | None = None
        begun: dict[str, Any] | None = None
        try:
            context = self.context(run_id)
            with tracing_context(enabled=False):  # an analysis run is never sent to LangSmith
                snapshot = self.graph.get_state(config)
            run_input: Any
            if snapshot.next:  # carry on from the last checkpoint (a retry, or a restart)
                run_input = None
                begun = snapshot.values
                self._resume(run_id)
            else:
                run_input = {
                    "run_id": run_id,
                    "job_id": job.id,
                    "statement_ids": statement_ids,
                    "triggers": triggers(statement_ids, reasons),
                }
            with tracing_context(enabled=False):
                out = self.graph.invoke(run_input, config, context=context, durability="sync")
        except Exception:
            # The last resort: a bug or a database problem (an AI problem never gets here).
            # The job is retried and carries on from the last checkpoint; after its last try
            # nothing will, so its checkpoints go.
            last = int(job.attempts) >= int(job.max_attempts)
            with contextlib.suppress(sqlite3.Error):
                self._failed(job, run_id, statement_ids, reasons, context, last=last)
            if last:
                with contextlib.suppress(sqlite3.Error):
                    self.checkpointer.delete_thread(self.thread_id(job.id))
            raise
        self.checkpointer.delete_thread(self.thread_id(job.id))
        if begun is not None:
            self._follow_up(begun, statement_ids, reasons)
        return {"run_id": run_id, "summary": out.get("summary", "")}

    def _resume(self, run_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE analysis_run SET status = 'running', finished_at = NULL, summary = '',"
                " stopped_reason = NULL WHERE id = ?",
                [run_id],
            )

    def _failed(
        self,
        job: Any,
        run_id: str,
        statement_ids: list[str],
        reasons: list[str],
        context: AnalysisContext | None,
        *,
        last: bool,
    ) -> None:
        """Mark the run failed, adding what this attempt spent on the AI. A run that failed
        before it began (its budget couldn't be made) is recorded all the same."""
        calls, tokens, cost = usage(context) if context is not None else (0, 0, 0.0)
        summary = (
            f"Something went wrong during this run, so it stopped after {job.attempts} tries."
            " The next run picks up what's left."
            if last
            else WILL_RETRY
        )
        now = to_iso(self.deps.clock())
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO analysis_run (id, job_id, triggers, statement_ids, status,"
                " knowledge_version_start, summary, llm_calls, tokens, cost_gbp, stopped_reason,"
                " started_at, finished_at) VALUES (?, ?, ?, ?, 'failed', ?, ?, ?, ?, ?, 'error',"
                " ?, ?) ON CONFLICT(id) DO UPDATE SET status = 'failed',"
                " summary = excluded.summary, stopped_reason = 'error',"
                " llm_calls = llm_calls + excluded.llm_calls,"
                " tokens = tokens + excluded.tokens, cost_gbp = cost_gbp + excluded.cost_gbp,"
                " finished_at = excluded.finished_at",
                [
                    run_id,
                    job.id,
                    json.dumps(triggers(statement_ids, reasons)),
                    json.dumps(statement_ids),
                    self.deps.versions.current_in(conn),
                    summary,
                    calls,
                    tokens,
                    cost,
                    now,
                    now,
                ],
            )

    def _follow_up(
        self, begun: dict[str, Any], statement_ids: list[str], reasons: list[str]
    ) -> None:
        """A retried job may have gathered more work while it waited (new statements, the
        person's changes merged into it); the run that carried on had begun without them, so
        they get a run of their own."""
        for statement_id in sorted(set(statement_ids) - set(begun.get("statement_ids", []))):
            enqueue_analysis(self.queue, statement_id)
        for reason in sorted(set(reasons) - set(begun.get("triggers", []))):
            request_analysis(self.queue, reason)

    def sweep(self) -> int:
        """At start-up, after the ingest sweep (and the queue's recovery of interrupted jobs):
        drop the checkpoints of analysis runs whose job has gone (done, failed, cancelled or
        pruned), and close the runs a stop left `running` that nothing will carry on. A
        queued or running job keeps its thread, so a run a restart interrupted carries on.

        An interrupted job that couldn't be queued again because another analysis job was
        waiting was folded into that one (its statements and reasons merged): that job
        analyses them afresh, and rows already filed aren't sent to the AI again. Returns how
        many checkpoint threads were dropped."""
        jobs = {
            job.id
            for status in ("queued", "running")
            for job in self.queue.list(status=status, limit=100_000)
            if job.kind == ANALYSIS_JOB
        }
        live = {self.thread_id(job_id) for job_id in jobs}
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE analysis_run SET status = 'failed', stopped_reason = 'interrupted',"
                " summary = ?, finished_at = ? WHERE status = 'running'"
                " AND (job_id IS NULL OR job_id NOT IN (SELECT value FROM json_each(?)))",
                [INTERRUPTED, to_iso(self.deps.clock()), json.dumps(sorted(jobs))],
            )
        with self.checkpointer.cursor(transaction=False) as cur:
            threads = [r[0] for r in cur.execute("SELECT DISTINCT thread_id FROM checkpoints")]
        dropped = 0
        for thread_id in threads:
            if thread_id.startswith("analysis:") and thread_id not in live:
                self.checkpointer.delete_thread(thread_id)
                dropped += 1
        return dropped

    def request(self, reason: str, *, now: bool = False) -> int:
        job_id = request_analysis(self.queue, reason, debounce_s=0 if now else 30)
        if now:
            self.queue.expedite(ANALYSIS_JOB, scope_key=ANALYSIS_SCOPE)
        return job_id

    def runs(self, *, limit: int = 10) -> list[AnalysisRun]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM analysis_run ORDER BY started_at DESC, rowid DESC LIMIT ?", [limit]
            ).fetchall()
        out: list[AnalysisRun] = []
        for r in rows:
            data = dict(r)
            for key in ("triggers", "statement_ids", "counts"):
                data[key] = json.loads(data[key])
            out.append(AnalysisRun.model_validate(data))
        return out

    def waiting(self) -> dict[str, int]:
        with self.db.connection() as conn:
            return waiting_rows(conn)
