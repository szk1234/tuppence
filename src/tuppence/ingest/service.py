"""Statement uploads, the ingest job, the account answer and the fix-up screen (spec §6).

Task 10's routes call this service:

- `upload(filename, data)` stores a file and queues its `ingest` job (`UploadRejected` has a
  message for the person; a file already uploaded comes back with `duplicate=True`);
- `answer_account(id, account_id=…, expected_version=…)` answers "Which account is this?";
- `change_account(id, account_id=…, expected_version=…)` is "Wrong account?": the statement is
  read again for the chosen account (an imported one keeps its rows until the new read is
  imported, which replaces them);
- `save_draft(...)` and `accept(...)` are the fix-up screen's save and import;
- `retry(...)` reads a statement again; `delete(id)` removes it.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

from langgraph.types import Command
from langsmith import tracing_context
from pydantic import BaseModel, Field

from tuppence.core.errors import InputError, safe_error_text
from tuppence.core.money import MAX_PENCE
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.secrets import SecretError
from tuppence.ingest.check import balance_verified, check_rows, check_statement
from tuppence.ingest.clock import Deadline, after
from tuppence.ingest.files import StatementFiles
from tuppence.ingest.handoff import ANALYSIS_JOB
from tuppence.ingest.models import CheckLevel, Document, ParsedRow, ParsedStatement, SkippedLine
from tuppence.ingest.parse import LOCAL_SECONDS, held_back_message, too_long_message
from tuppence.ingest.pipeline import READ_FAILED, IngestGraph, RunContext, hand_off
from tuppence.ingest.registry import CsvLayout
from tuppence.ingest.sniff import UploadRejected, check_zip, sniff
from tuppence.ingest.store import FINISHED, IN_PROGRESS, StatementRecord, StatementStore
from tuppence.ingest.textnum import pounds
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
    """One row as the person left it on the fix-up screen. `ref` is a row of the draft, or a
    line held back from the AI (`draft["document"]["held_amount_refs"]`) that the person says
    is a transaction: they type it in here, and nothing is sent anywhere."""

    ref: str = Field(min_length=1, max_length=40)
    date: dt.date
    amount_pence: int = Field(ge=-MAX_PENCE, le=MAX_PENCE)
    description: str = Field(min_length=1, max_length=300)


def clean_filename(name: str | None) -> str:
    base = (name or "statement").replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return (base or "statement")[:255]


def recheck(
    doc: Document,
    parsed: ParsedStatement,
    level: CheckLevel,
    *,
    deadline: Deadline | None = None,
) -> list[str]:
    """Every check again after the person's edits. Rows they changed skip the line checks.
    A held-back line counts once the person makes it a row or skips it; until then it is
    reported, as the parse step did."""
    used = {r.ref for r in parsed.rows} | {s.ref for s in parsed.skipped}
    decided = [r for r in doc.held_amount_refs if r in used]
    long = set(doc.too_long_refs)
    errors = check_rows(
        doc.lines,
        all_lines=doc.lines,
        context_refs=doc.header_refs,
        data_refs=[*doc.data_refs, *decided],
        parsed=parsed,
        level=level,
        deadline=deadline,
    )
    errors += check_statement(parsed, level=level, dates=True, deadline=deadline)
    held_left = [r for r in doc.held_amount_refs if r not in used and r not in long]
    if held := held_back_message(doc, len(held_left)):
        errors.append(held)
    if too_long := too_long_message(doc, sum(1 for r in long if r not in used)):
        errors.append(too_long)
    return list(dict.fromkeys(errors))


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
        """At start-up: queue every statement left part-way; it continues from its checkpoint,
        with the person's answer if they had given one."""
        ids = self.store.unfinished()
        for statement_id in ids:
            answer = self.store.get(statement_id).account_answer_id
            self._enqueue(statement_id, {"account_id": answer} if answer else None)
        return len(ids)

    def sweep(self) -> dict[str, int]:
        """At start-up, after `resume_unfinished`: queue the analysis of imported statements
        that have no analysis job (the app stopped between the import and the hand-off), and
        drop run checkpoints that no statement needs any more."""
        waiting: set[str] = set()
        for status in ("queued", "running"):
            for job in self.queue.list(status=status, limit=1000):
                if job.kind == ANALYSIS_JOB:
                    waiting.update(job.payload.get("statement_ids", []))
        queued = 0
        for statement_id in self.store.pending_analysis():
            if statement_id not in waiting:
                hand_off(self.deps.on_imported, statement_id)
                queued += 1
        live: set[str] = set()
        for record in self.store.list(limit=100_000):
            if record.status in (*IN_PROGRESS, "needs_account"):
                live.add(self.thread_id(record))
        with self.checkpointer.cursor(transaction=False) as cur:
            threads = [r[0] for r in cur.execute("SELECT DISTINCT thread_id FROM checkpoints")]
        dropped = 0
        for thread_id in threads:
            if thread_id not in live:
                self.checkpointer.delete_thread(thread_id)
                dropped += 1
        if dropped:
            self._shrink_checkpoints()
        return {"analysis_queued": queued, "checkpoints_dropped": dropped}

    # --- the job ---------------------------------------------------------------------------

    @staticmethod
    def thread_id(record: StatementRecord) -> str:
        return f"statement:{record.id}:{record.run}"

    def _shrink_checkpoints(self) -> None:
        """Fold the write-ahead log into checkpoints.db and empty it. With secure_delete on,
        deleted runs are overwritten, so no statement text is left on disk."""
        with self.checkpointer.cursor(transaction=False) as cur:
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _forget(self, record: StatementRecord, run: int | None = None) -> None:
        """Drop a run's checkpoints: they hold the statement's text and aren't needed once the
        run has finished (a retry starts a new run)."""
        thread = f"statement:{record.id}:{record.run if run is None else run}"
        self.checkpointer.delete_thread(thread)
        self._shrink_checkpoints()

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
            self._forget(record)
            return {"statement_id": statement_id, "status": record.status}
        config: dict[str, Any] = {
            "configurable": {"thread_id": self.thread_id(record)},
            "recursion_limit": RECURSION_LIMIT,
        }
        # The answer to "Which account is this?" is kept on the statement row, so a restart or
        # a re-queued job can't lose it; a job's own copy is only a fallback.
        answer = record.account_answer_id or (job.payload.get("resume") or {}).get("account_id")
        context: RunContext | None = None
        try:
            context = RunContext(run=self.budget_factory())
            with tracing_context(enabled=False):  # a run is never sent to LangSmith
                snapshot = self.graph.get_state(config)
            run_input: Any
            if snapshot.interrupts and not answer:
                # Still waiting for "Which account is this?" and no answer came: ask again.
                return self._ask(statement_id, snapshot.interrupts[0].value)
            if answer and (snapshot.interrupts or "choose_account" in (snapshot.next or ())):
                # The answer. If the run stopped just before asking, it is the answer to the
                # question it is about to ask.
                run_input = Command(resume={"account_id": answer})
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

    def _check_account(self, account_id: str) -> None:
        if account_id not in self._active_accounts():
            raise InputError("Choose one of your open accounts, or add the account first.")

    def answer_account(
        self, statement_id: str, *, account_id: str, expected_version: int
    ) -> StatementRecord:
        """The answer to "Which account is this?". It is kept on the statement, and the run
        carries on in the background."""
        record = self.store.get(statement_id)
        if record.status != "needs_account":
            raise InputError("This statement isn't waiting for an answer any more.")
        self._check_account(account_id)
        record = self.store.update_versioned(
            statement_id,
            expected_version,
            status="parsing",
            question=None,
            account_answer_id=account_id,
            account_answer_version=expected_version + 1,
        )
        self._enqueue(statement_id, resume={"account_id": account_id})
        return record

    def change_account(
        self, statement_id: str, *, account_id: str, expected_version: int
    ) -> StatementRecord:
        """ "Wrong account?": read the statement again as the chosen account's. An imported
        statement keeps its rows until the new read is imported, which replaces them in one
        transaction (rows another statement covers stay); the new run starts from the file,
        since the account's type decides which way round the amounts are read."""
        record = self.store.get(statement_id)
        if record.status == "needs_account":  # not read yet: this is just the answer
            return self.answer_account(
                statement_id, account_id=account_id, expected_version=expected_version
            )
        if record.status not in FINISHED:
            raise InputError(
                "This statement is still being read. Change its account once it has finished."
            )
        self._check_account(account_id)
        return self._restart(record, expected_version, account_id)

    def _restart(
        self, record: StatementRecord, expected_version: int, account_id: str | None
    ) -> StatementRecord:
        answer: dict[str, Any] = {}
        if account_id is not None:
            answer = {
                "account_answer_id": account_id,
                "account_answer_version": expected_version + 1,
            }
        reopened = self.store.reopen(
            record.id,
            expected_version,
            status="received",
            run=record.run + 1,
            account_id=None,
            error=None,
            draft=None,
            check_errors=[],
            question=None,
            **answer,
        )
        self._forget(record)  # the old run's checkpoints, if any are left
        self._enqueue(record.id)
        return reopened

    def save_draft(
        self,
        statement_id: str,
        *,
        rows: Sequence[RowEdit],
        skipped: Sequence[SkippedLine],
        expected_version: int,
    ) -> StatementRecord:
        """Keep the person's edits and check the statement again. Each line is a row once, or
        skipped once; a held-back line may become a row the person typed in."""
        record = self.store.get(statement_id)
        if record.status != "needs_review" or record.draft is None:
            raise InputError("Only statements that need your check can be edited.")
        doc = Document.model_validate(record.draft["document"])
        parsed = ParsedStatement.model_validate(record.draft["parsed"])
        original = {row.ref: row for row in parsed.rows}
        held = set(doc.held_amount_refs)
        seen: set[str] = set()
        kept: list[ParsedRow] = []
        for edit in rows:
            if edit.ref in seen:
                raise InputError(f"Line {edit.ref} is listed more than once.")
            seen.add(edit.ref)
            if edit.amount_pence == 0:
                raise InputError("An amount can't be zero. Tick 'Not a transaction' instead.")
            if abs(edit.amount_pence) > MAX_PENCE:
                raise InputError("That amount is too large.")
            base = original.get(edit.ref)
            if base is None and edit.ref in held:  # a held-back line the person typed in
                base = ParsedRow(
                    ref=edit.ref,
                    date=edit.date,
                    amount_pence=edit.amount_pence,
                    amount_text=pounds(abs(edit.amount_pence)),
                    raw_description=edit.description,
                    edited=True,
                )
            if base is None:
                raise InputError(f"Line {edit.ref} isn't a transaction on this statement.")
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
        known = set(doc.data_refs) | set(original) | held
        skipped_refs: set[str] = set()
        for skip in skipped:
            if skip.ref not in known:
                raise InputError(f"Line {skip.ref} isn't part of this statement.")
            if skip.ref in seen:
                raise InputError(f"Line {skip.ref} can't be a transaction and skipped too.")
            if skip.ref in skipped_refs:
                raise InputError(f"Line {skip.ref} is listed more than once.")
            skipped_refs.add(skip.ref)
        parsed.rows, parsed.skipped = kept, list(skipped)
        level = _level(record.draft)
        draft = {**record.draft, "parsed": parsed.model_dump(mode="json")}
        return self.store.update_versioned(
            statement_id,
            expected_version,
            draft=draft,
            check_errors=recheck(doc, parsed, level, deadline=after(LOCAL_SECONDS)),
        )

    def accept(self, statement_id: str, *, expected_version: int) -> StatementRecord:
        """Import the draft as the person left it, even if some checks still fail. A learned
        layout waiting for this confirmation is saved in the same write (unless they turned
        its signs round)."""
        record = self.store.get(statement_id)
        if record.status != "needs_review" or record.draft is None or record.account_id is None:
            raise InputError("Only statements that need your check can be imported from here.")
        if record.version != expected_version:
            raise VersionConflict("statement", statement_id, expected_version, record.version)
        if record.account_id not in self._active_accounts():
            raise InputError(
                "The account for this statement is closed. Reopen it, or choose another account."
            )
        parsed = ParsedStatement.model_validate(record.draft["parsed"])
        level = _level(record.draft)
        stats = {**record.stats, "accepted_with": list(record.check_errors)}
        layout = confirmed_layout(record.draft, parsed)
        within: Callable[[sqlite3.Connection], object] | None = None
        if layout is not None:
            parsed = parsed.model_copy(update={"importer": f"csv:{layout.id}"})
            doc = Document.model_validate(record.draft["document"])
            within = partial(self._save_layout, doc, layout)
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
            within=within,
            deadline=after(LOCAL_SECONDS, "Importing this statement"),
        )
        hand_off(self.deps.on_imported, statement_id)
        self._forget(record)
        return self.store.get(statement_id)

    def _save_layout(self, doc: Document, layout: CsvLayout, conn: sqlite3.Connection) -> None:
        # `layout.kind` is the account type it was proposed for: it is remembered on that side
        # only (a bank account's or a card's).
        self.deps.registry.save_learned(doc, layout, conn=conn)

    def retry(self, statement_id: str, *, expected_version: int) -> StatementRecord:
        """Read the file again from the start, as a new run. An imported statement keeps its
        rows until the new read is imported (and if it fails); rows that come back unchanged
        keep their ids. An account the person chose is kept; "Wrong account?"
        (`change_account`) chooses another."""
        record = self.store.get(statement_id)
        if record.status not in FINISHED:
            raise InputError("This statement is still being read.")
        return self._restart(record, expected_version, None)

    def delete(self, statement_id: str) -> None:
        """Remove a statement, its balances, the rows no other statement covers, its run state
        and its stored file."""
        record = self.store.delete(statement_id)
        self._forget(record)
        self.files.delete(record.file_sha256, record.file_ext)
