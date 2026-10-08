"""Statement uploads, the ingest job, the account answer and the fix-up screen (spec §6).

Task 10's routes call this service:

- `upload(filename, data)` stores a file and queues its `ingest` job (`UploadRejected` has a
  message for the person; a file already uploaded comes back with `duplicate=True`);
- `answer_account(id, account_id=…, expected_version=…)` answers "Which account is this?";
- `save_draft(...)` and `accept(...)` are the fix-up screen's save and import;
- `retry(...)` reads a failed or unchecked statement again; `delete(id)` removes it.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langgraph.types import Command
from langsmith import tracing_context
from pydantic import BaseModel, Field

from tuppence.core.errors import InputError, safe_error_text
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.secrets import SecretError
from tuppence.ingest.check import balance_verified, check_rows, check_statement
from tuppence.ingest.files import StatementFiles
from tuppence.ingest.models import CheckLevel, Document, ParsedRow, ParsedStatement, SkippedLine
from tuppence.ingest.pipeline import READ_FAILED, IngestGraph, RunContext, hand_off
from tuppence.ingest.registry import CsvLayout
from tuppence.ingest.sniff import UploadRejected, check_zip, sniff
from tuppence.ingest.store import FINISHED, StatementRecord, StatementStore
from tuppence.llm.types import LLMError

INGEST_JOB = "ingest"
RECURSION_LIMIT = 25
SOMETHING_WRONG = (
    "Something went wrong while reading this file. Press Try again, or remove it and upload "
    "it again."
)
SIGNS_CHANGED = "You changed which way round some amounts are, so this layout wasn't remembered."


@dataclass(frozen=True)
class UploadOutcome:
    record: StatementRecord
    duplicate: bool


class RowEdit(BaseModel):
    """One row as the person left it on the fix-up screen."""

    ref: str
    date: dt.date
    amount_pence: int
    description: str = Field(min_length=1, max_length=300)


def clean_filename(name: str | None) -> str:
    base = (name or "statement").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return (base or "statement")[:255]


def recheck(doc: Document, parsed: ParsedStatement, level: CheckLevel) -> list[str]:
    """Every check again after the person's edits. Rows they changed skip the line checks."""
    errors = check_rows(
        doc.lines,
        all_lines=doc.lines,
        context_refs=doc.header_refs,
        data_refs=doc.data_refs,
        parsed=parsed,
        level=level,
    )
    return list(dict.fromkeys(errors + check_statement(parsed, level=level, dates=True)))


def _level(draft: dict[str, Any]) -> CheckLevel:
    return "screenshot" if draft.get("level") == "screenshot" else "full"


def confirmed_layout(draft: dict[str, Any], parsed: ParsedStatement) -> CsvLayout | None:
    """The learned layout waiting in a draft, if the person kept the signs it read. One whose
    signs they turned round is wrong, so it isn't remembered."""
    pending = draft.get("pending_layout")
    if pending is None:
        return None
    proposed: dict[str, bool] = draft.get("proposed_signs") or {}
    for row in parsed.rows:
        if proposed.get(row.ref, row.amount_pence > 0) != (row.amount_pence > 0):
            return None
    return CsvLayout.model_validate(pending)


class IngestService:
    def __init__(
        self,
        *,
        store: StatementStore,
        files: StatementFiles,
        graph: IngestGraph,
        checkpointer: Any,
        queue: Any,
        budget_factory: Callable[[], Any],
    ) -> None:
        self.store, self.files, self.queue, self.checkpointer = store, files, queue, checkpointer
        self.deps = graph.d
        self.graph = graph.build(checkpointer)
        self.budget_factory = budget_factory  # () -> a RunBudget from the reader manifest
        self._closing = False

    def close(self) -> None:
        """At shutdown, once the worker has stopped: close `checkpoints.db`. A job the worker
        stopped waiting for leaves its statement part-way; it carries on at the next start."""
        self._closing = True
        self.checkpointer.conn.close()

    # --- uploads ---------------------------------------------------------------------------

    def upload(self, filename: str | None, data: bytes) -> UploadOutcome:
        """Store one file and queue it. Raises UploadRejected with a message for the person."""
        sniffed = sniff(data[:65536])
        existing = self.store.find_by_sha(hashlib.sha256(data).hexdigest())
        if existing is not None:
            return UploadOutcome(existing, duplicate=True)
        sha, path = self.files.save(data, sniffed.ext)
        if sniffed.kind == "xlsx":
            try:
                check_zip(path)
            except UploadRejected:
                self.files.delete(sha, sniffed.ext)
                raise
        try:
            record = self.store.create(
                sha256=sha, ext=sniffed.ext, filename=clean_filename(filename), kind=sniffed.kind
            )
        except sqlite3.IntegrityError:  # the same file arrived twice at once
            found = self.store.find_by_sha(sha)
            if found is None:
                raise
            return UploadOutcome(found, duplicate=True)
        self._enqueue(record.id)
        return UploadOutcome(record, duplicate=False)

    def _enqueue(self, statement_id: str, resume: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"statement_id": statement_id}
        if resume is not None:
            payload["resume"] = resume
        self.queue.enqueue(INGEST_JOB, scope_key=statement_id, payload=payload, max_attempts=1)

    def resume_unfinished(self) -> int:
        """At start-up: queue every statement left part-way; it continues from its checkpoint."""
        ids = self.store.unfinished()
        for statement_id in ids:
            self._enqueue(statement_id)
        return len(ids)

    # --- the job ---------------------------------------------------------------------------

    @staticmethod
    def thread_id(record: StatementRecord) -> str:
        return f"statement:{record.id}:{record.run}"

    def _forget(self, record: StatementRecord) -> None:
        """Drop a run's checkpoints: they hold the statement's text and aren't needed once the
        run has finished (a retry starts a new run)."""
        self.checkpointer.delete_thread(self.thread_id(record))

    def _fail(self, statement_id: str, message: str) -> None:
        """Mark a run failed, unless it was removed or already imported (its rows are stored:
        nothing after the import may say otherwise)."""
        with contextlib.suppress(NotFound):
            if self.store.get(statement_id).status != "imported":
                self.store.update(statement_id, status="failed", error=message)

    def _ask(self, statement_id: str, question: Any) -> dict[str, Any]:
        record = self.store.get(statement_id)
        if record.status != "needs_account" or record.question != question:
            self.store.update(statement_id, status="needs_account", question=question)
        return {"statement_id": statement_id, "status": "needs_account"}

    def handle_job(self, job: Any) -> dict[str, Any]:
        """Run (or carry on) a statement's ingest graph. Expected failures end the run with a
        plain message on the statement; only a bug is raised, after marking it failed."""
        statement_id = job.payload["statement_id"]
        try:
            record = self.store.get(statement_id)
        except NotFound:
            return {"statement_id": statement_id, "status": "removed"}
        if record.status in FINISHED:  # an old job for a run that has ended: nothing to do
            return {"statement_id": statement_id, "status": record.status}
        config: dict[str, Any] = {
            "configurable": {"thread_id": self.thread_id(record)},
            "recursion_limit": RECURSION_LIMIT,
        }
        context: RunContext | None = None
        try:
            context = RunContext(run=self.budget_factory())
            with tracing_context(enabled=False):  # a run is never sent to LangSmith
                snapshot = self.graph.get_state(config)
            resume = job.payload.get("resume")
            run_input: Any
            if resume is None and snapshot.interrupts:
                # Still waiting for "Which account is this?" and no answer came: ask again.
                return self._ask(statement_id, snapshot.interrupts[0].value)
            if resume is not None and (snapshot.interrupts or snapshot.next):
                # The answer. If the run stopped just before asking, it is the answer to the
                # question it is about to ask.
                run_input = Command(resume=resume)
            elif snapshot.next:
                run_input = None  # carry on from the last checkpoint after a restart
            else:
                run_input = {"statement_id": statement_id}
            with tracing_context(enabled=False):
                self.graph.invoke(run_input, config, context=context, durability="sync")
                waiting = self.graph.get_state(config).interrupts
            if waiting:  # the run stopped at the question: make sure the statement shows it
                return self._ask(statement_id, waiting[0].value)
        except NotFound:  # removed while it was being read
            self._forget(record)
            return {"statement_id": statement_id, "status": "removed"}
        except (LLMError, SecretError) as exc:  # the steps catch these; never a crash anyway
            self._fail(statement_id, f"{READ_FAILED} {safe_error_text(exc)}")
        except Exception as exc:
            if self._closing:  # shutting down: resume_unfinished() picks the statement up again
                raise
            self._fail(statement_id, f"{SOMETHING_WRONG} ({type(exc).__name__})")
            with contextlib.suppress(sqlite3.Error):
                self._forget(record)
            raise
        try:
            status = self.store.get(statement_id).status
        except NotFound:
            status = "removed"
        if status in (*FINISHED, "removed"):
            self._forget(record)
        run = context.run if context is not None else None
        return {
            "statement_id": statement_id,
            "status": status,
            "llm_calls": run.calls if run is not None else 0,
            "cost_gbp": round(run.gbp, 4) if run is not None else 0.0,
        }

    # --- the person's actions --------------------------------------------------------------

    def _active_accounts(self) -> set[str]:
        return {
            a.id for a in self.deps.accounts.list() if getattr(a, "status", "active") == "active"
        }

    def answer_account(
        self, statement_id: str, *, account_id: str, expected_version: int
    ) -> StatementRecord:
        """The answer to "Which account is this?". The run carries on in the background."""
        record = self.store.get(statement_id)
        if record.status != "needs_account":
            raise InputError("This statement isn't waiting for an answer any more.")
        if account_id not in self._active_accounts():
            raise InputError("Choose one of your open accounts, or add the account first.")
        record = self.store.update_versioned(
            statement_id, expected_version, status="parsing", question=None
        )
        self._enqueue(statement_id, resume={"account_id": account_id})
        return record

    def save_draft(
        self,
        statement_id: str,
        *,
        rows: Sequence[RowEdit],
        skipped: Sequence[SkippedLine],
        expected_version: int,
    ) -> StatementRecord:
        """Keep the person's edits and check the statement again."""
        record = self.store.get(statement_id)
        if record.status != "needs_review" or record.draft is None:
            raise InputError("Only statements that need your check can be edited.")
        doc = Document.model_validate(record.draft["document"])
        parsed = ParsedStatement.model_validate(record.draft["parsed"])
        original = {row.ref: row for row in parsed.rows}
        kept: list[ParsedRow] = []
        for edit in rows:
            base = original.get(edit.ref)
            if base is None:
                raise InputError(f"Line {edit.ref} isn't a transaction on this statement.")
            if edit.amount_pence == 0:
                raise InputError("An amount can't be zero. Tick 'Not a transaction' instead.")
            changed = (base.date, base.amount_pence, base.raw_description) != (
                edit.date,
                edit.amount_pence,
                edit.description,
            )
            kept.append(
                base.model_copy(
                    update={
                        "date": edit.date,
                        "amount_pence": edit.amount_pence,
                        "raw_description": edit.description,
                        "edited": base.edited or changed,
                    }
                )
            )
        known = set(doc.data_refs) | set(original)
        for skip in skipped:
            if skip.ref not in known:
                raise InputError(f"Line {skip.ref} isn't part of this statement.")
        parsed.rows, parsed.skipped = kept, list(skipped)
        level = _level(record.draft)
        draft = {**record.draft, "parsed": parsed.model_dump(mode="json")}
        return self.store.update_versioned(
            statement_id, expected_version, draft=draft, check_errors=recheck(doc, parsed, level)
        )

    def accept(self, statement_id: str, *, expected_version: int) -> StatementRecord:
        """Import the draft as the person left it, even if some checks still fail. A learned
        layout waiting for this confirmation is remembered now (unless they turned its signs
        round)."""
        record = self.store.get(statement_id)
        if record.status != "needs_review" or record.draft is None or record.account_id is None:
            raise InputError("Only statements that need your check can be imported from here.")
        if record.version != expected_version:
            raise VersionConflict("statement", statement_id, expected_version, record.version)
        if record.account_id not in self._active_accounts():
            raise InputError(
                "The account for this statement is closed. Reopen it, or press Try again."
            )
        parsed = ParsedStatement.model_validate(record.draft["parsed"])
        level = _level(record.draft)
        stats = {**record.stats, "accepted_with": list(record.check_errors)}
        layout = confirmed_layout(record.draft, parsed)
        if layout is not None:
            parsed = parsed.model_copy(update={"importer": f"csv:{layout.id}"})
        elif record.draft.get("pending_layout") is not None:
            parsed = parsed.model_copy(update={"importer": "csv:unknown"})
            stats["layout_not_kept"] = SIGNS_CHANGED
        self.store.persist(
            statement_id,
            record.account_id,
            parsed,
            balance_verified=balance_verified(parsed, record.check_errors, level),
            stats=stats,
            expected_version=expected_version,
        )
        if layout is not None:
            self.deps.registry.save_learned(
                Document.model_validate(record.draft["document"]), layout
            )
        hand_off(self.deps.on_imported, statement_id)
        self._forget(record)
        return self.store.get(statement_id)

    def retry(self, statement_id: str, *, expected_version: int) -> StatementRecord:
        """Read the file again from the start, as a new run."""
        record = self.store.get(statement_id)
        if record.status not in ("failed", "needs_review"):
            raise InputError("Only statements that failed or need your check can be read again.")
        record = self.store.update_versioned(
            statement_id,
            expected_version,
            status="received",
            run=record.run + 1,
            error=None,
            draft=None,
            check_errors=[],
            question=None,
        )
        self.checkpointer.delete_thread(f"statement:{record.id}:{record.run - 1}")
        self._enqueue(statement_id)
        return record

    def delete(self, statement_id: str) -> None:
        """Remove a statement, its transactions, balances, run state and stored file."""
        record = self.store.delete(statement_id)
        self._forget(record)
        self.files.delete(record.file_sha256, record.file_ext)
