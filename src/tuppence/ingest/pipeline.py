"""The ingest graph: extract → identify → choose account → parse → finish (spec §6.2).

A fixed LangGraph workflow. Code decides every step; the only loops are the bounded read
retries inside `parse` (at most 3 attempts per chunk). Runs are checkpointed in
`checkpoints.db`, so a run survives a restart, and the account question is an `interrupt()`
resumed by the answer.

Every error a step can expect ends the run with a plain message on the statement: a file
Tuppence won't read, an AI model that isn't set up, blocked by Local only, waiting for its
cloud notice, out of budget or failing. Only a bug reaches `IngestService.handle_job`.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from tuppence.core.errors import UserFacing, safe_error_text
from tuppence.core.secrets import SecretError
from tuppence.ingest.check import balance_verified
from tuppence.ingest.extract import ExtractLimits, extract_document
from tuppence.ingest.identify import AccountRef, Evidence, identify, match_account
from tuppence.ingest.models import AccountKind, CheckLevel, Document, ParsedStatement
from tuppence.ingest.parse import ReaderLimits, parse_document
from tuppence.ingest.registry import BankPack, LayoutRegistry
from tuppence.llm.types import LLMError, NoModelConfigured

log = logging.getLogger("tuppence.ingest")

NO_MODEL = (
    "This file needs an AI model to read it. Choose one in Settings › AI, then press Try again."
)
VISION_FAILED = "The AI vision model couldn't read this file."
READ_FAILED = "The AI model couldn't finish reading this file."
ACCOUNT_GONE = (
    "The account chosen for this statement is no longer available. "
    "Press Try again to choose another."
)
QUESTION = "Which account is this?"


class IngestState(TypedDict):
    """The run's state, saved after every step. Plain JSON values only.

    Keys arrive step by step (LangGraph doesn't require them all up front); a step reads
    only keys an earlier step wrote.
    """

    statement_id: str
    document: dict[str, Any]
    evidence: dict[str, Any]
    account_id: str
    account_kind: str
    parsed: dict[str, Any]
    errors: list[str]
    level: str
    info: dict[str, Any]
    pending_layout: dict[str, Any] | None
    question: dict[str, Any]
    failure: str
    outcome: str


@dataclass
class RunContext:
    run: Any  # a RunBudget for this run, shared by every AI call in it


def _no_names() -> list[str]:
    return []


@dataclass
class IngestDeps:
    store: Any  # StatementStore
    files: Any  # StatementFiles
    pack: BankPack
    registry: LayoutRegistry
    accounts: Any  # AccountService: .list() gives active accounts with id, provider, kind…
    llm: Any  # LLMClient
    router: Any  # TaskRouter
    config: Any  # ConfigService: .get("reader") -> AgentManifest
    settings: Any  # SettingsStore
    on_imported: Callable[[str], object]  # hands the statement to the analysis workflow
    fingerprint_key: Callable[[], bytes] = field(repr=False)  # identify.fingerprint_key(db)
    names: Callable[[], Sequence[str]] = _no_names  # the household's names, retired ones too
    prompts_dir: Path | None = None
    today: Callable[[], dt.date] = dt.date.today
    vision_factory: Callable[[Any], Any] | None = None  # run budget → VisionReader, or None


def _failure(prefix: str, exc: BaseException) -> dict[str, Any]:
    return {"failure": f"{prefix} {safe_error_text(exc)}"}


class IngestGraph:
    def __init__(self, deps: IngestDeps) -> None:
        self.d = deps

    # --- helpers ---------------------------------------------------------------------------

    def _limits(self) -> dict[str, int]:
        return dict(self.d.config.get("reader").limits)

    def _extract_limits(self) -> ExtractLimits:
        limits = self._limits()
        return ExtractLimits(
            max_pages=limits.get("max_pages", 50),
            timeout_s=limits.get("extract_timeout_seconds", 180),
            memory_mb=limits.get("extract_memory_mb", 2048),
        )

    def _names(self) -> list[str]:
        return [n for n in self.d.names() if n.strip()]

    def _account_refs(self) -> list[AccountRef]:
        return [
            AccountRef(
                id=a.id,
                provider=a.provider,
                provider_name=a.provider_name,
                kind=a.kind,
                nickname=a.nickname,
                last4=a.last4,
                status=a.status,
            )
            for a in self.d.accounts.list()
        ]

    # --- nodes -----------------------------------------------------------------------------

    def extract(self, state: IngestState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        record = self.d.store.update(state["statement_id"], status="identifying")
        vision = None
        try:
            if self.d.vision_factory is not None and self.d.settings.get("ingest.vision_for_scans"):
                vision = self.d.vision_factory(runtime.context.run)
            doc = extract_document(
                self.d.files.path_for(record.file_sha256, record.file_ext),
                record.format,
                sha256=record.file_sha256,
                limits=self._extract_limits(),
                known_header=self.d.registry.is_known_header,
                vision=vision,
                names=self._names(),
            )
        except NoModelConfigured:
            return {"failure": NO_MODEL}
        except (LLMError, SecretError) as exc:
            return _failure(VISION_FAILED, exc)
        except Exception as exc:
            if isinstance(exc, UserFacing):  # a file Tuppence won't read, in its own words
                return {"failure": safe_error_text(exc)}
            raise
        return {"document": doc.model_dump(mode="json")}

    def identify(self, state: IngestState) -> dict[str, Any]:
        record = self.d.store.get(state["statement_id"])
        doc = Document.model_validate(state["document"])
        evidence = identify(
            doc, pack=self.d.pack, registry=self.d.registry, key=self.d.fingerprint_key()
        )
        accounts = self._account_refs()
        match = match_account(
            evidence, accounts, self.d.store.remembered_accounts(evidence.layout_fingerprint)
        )
        fields: dict[str, Any] = {
            "layout_fingerprint": evidence.layout_fingerprint,
            "provider": evidence.providers[0] if evidence.providers else evidence.provider_hint,
        }
        answered = record.account_answer_id
        if answered and any(a.id == answered and a.status == "active" for a in accounts):
            # The person already said which account this is ("Wrong account?", or an answer
            # kept from an earlier run): that wins over any guess.
            match = match.model_copy(update={"account_id": answered})
        question: dict[str, Any] | None = None
        if match.account_id is None:
            question = {
                "kind": "identify_account",
                "text": QUESTION,
                "reason": match.reason,
                # the suggested account, pre-selected; None offers "+ new account" first
                "best_guess": match.best_guess,
                "candidates": match.candidates,
                "prefill": {**match.prefill, "nickname": evidence.label},
            }
            # An answer kept from before is for an account that has gone: forget it, so only
            # the answer to this question resumes the run.
            fields.update(
                status="needs_account",
                question=question,
                account_answer_id=None,
                account_answer_version=None,
            )
        self.d.store.update(state["statement_id"], **fields)
        return {
            "evidence": evidence.model_dump(mode="json"),
            "account_id": match.account_id or "",
            "question": question or {},
        }

    def choose_account(self, state: IngestState) -> dict[str, Any]:
        account_id = state.get("account_id") or ""
        if not account_id:
            # Waits here until IngestService.answer_account() resumes the run. The step runs
            # again from its start then, so nothing above this line may write anything.
            answer = interrupt(state.get("question") or {})
            account_id = str(answer.get("account_id", "")) if isinstance(answer, dict) else ""
        chosen = next(
            (a for a in self._account_refs() if a.id == account_id and a.status == "active"), None
        )
        if chosen is None:
            return {"failure": ACCOUNT_GONE}
        self.d.store.update(state["statement_id"], account_id=chosen.id, question=None)
        return {"account_id": chosen.id, "account_kind": chosen.kind}

    def parse(self, state: IngestState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        record = self.d.store.update(state["statement_id"], status="parsing")
        doc = Document.model_validate(state["document"])
        run = runtime.context.run
        limits = self._limits()
        kind = cast(AccountKind, state["account_kind"])  # stored as a plain string
        try:
            try:
                context_window: int | None = self.d.router.chain_for("read")[0][1].context_window
            except NoModelConfigured:
                context_window = None  # fine for fixed importers; an AI step will raise again
            outcome = parse_document(
                doc,
                self.d.files.path_for(record.file_sha256, record.file_ext),
                Evidence.model_validate(state["evidence"]),
                kind,
                registry=self.d.registry,
                llm=self.d.llm,
                run=run,
                context_window=context_window,
                today=self.d.today(),
                prompts_dir=self.d.prompts_dir,
                limits=ReaderLimits(
                    max_attempts_per_chunk=limits.get("max_attempts_per_chunk", 3),
                    rows_per_chunk=limits.get("rows_per_chunk", 40),
                    parallel_chunks=limits.get("parallel_chunks", 2),
                ),
                names=self._names(),
                extract_limits=self._extract_limits(),
            )
        except NoModelConfigured:
            return {"failure": NO_MODEL}
        except (LLMError, SecretError) as exc:
            return _failure(READ_FAILED, exc)
        except Exception as exc:
            if isinstance(exc, UserFacing):
                return {"failure": safe_error_text(exc)}
            raise
        pending = outcome.pending_layout
        return {
            "parsed": outcome.parsed.model_dump(mode="json"),
            "errors": outcome.errors,
            "level": outcome.level,
            "pending_layout": pending.model_dump(mode="json") if pending is not None else None,
            "info": {
                **outcome.info,
                "llm_calls": run.calls,
                "tokens": run.tokens,
                "cost_gbp": round(run.gbp, 4),
                "warnings": doc.warnings,
                "ocr_confidence": doc.ocr_confidence,
                "pages": doc.pages,
            },
        }

    def finish(self, state: IngestState) -> dict[str, Any]:
        statement_id = state["statement_id"]
        if state.get("failure"):
            self.d.store.update(statement_id, status="failed", error=state.get("failure"))
            return {"outcome": "failed"}
        parsed = ParsedStatement.model_validate(state["parsed"])
        errors = list(state.get("errors") or [])
        level: CheckLevel = "screenshot" if state.get("level") == "screenshot" else "full"
        info = state.get("info") or {}
        pending = state.get("pending_layout")
        if errors or pending is not None:
            # A check failed, or a learned layout is in doubt: the person looks first. A pending
            # layout is saved only when they confirm the statement (IngestService.accept).
            self.d.store.update(
                statement_id,
                status="needs_review",
                check_errors=errors,
                stats=info,
                importer=str(info.get("importer") or parsed.importer),
                draft={
                    "document": state["document"],
                    "parsed": state["parsed"],
                    "level": level,
                    "pending_layout": pending,
                    "proposed_signs": {r.ref: r.amount_pence > 0 for r in parsed.rows},
                },
            )
            return {"outcome": "needs_review"}
        self.d.store.persist(
            statement_id,
            state["account_id"],
            parsed,
            balance_verified=balance_verified(parsed, errors, level),
            stats=info,
        )
        hand_off(self.d.on_imported, statement_id)
        return {"outcome": "imported"}

    # --- wiring ----------------------------------------------------------------------------

    def build(self, checkpointer: BaseCheckpointSaver) -> Any:
        graph = StateGraph(IngestState, context_schema=RunContext)
        graph.add_node("extract", self.extract)
        graph.add_node("identify", self.identify)
        graph.add_node("choose_account", self.choose_account)
        graph.add_node("parse", self.parse)
        graph.add_node("finish", self.finish)
        graph.add_edge(START, "extract")
        graph.add_conditional_edges("extract", _ok_or_finish("identify"), ["identify", "finish"])
        graph.add_edge("identify", "choose_account")
        graph.add_conditional_edges("choose_account", _ok_or_finish("parse"), ["parse", "finish"])
        graph.add_edge("parse", "finish")
        graph.add_edge("finish", END)
        return graph.compile(checkpointer=checkpointer)


def hand_off(on_imported: Callable[[str], object], statement_id: str) -> None:
    """Queue the analysis of a statement that has just been imported. The import itself is
    already committed, with the statement's `analysis_state` set to "pending", so a failure
    here is logged and the analysis workflow finds the statement from that state instead."""
    try:
        on_imported(statement_id)
    except Exception:  # noqa: BLE001 - the import stands; the pending state is the record
        log.exception("couldn't queue the analysis of statement %s", statement_id)


def _ok_or_finish(next_node: str) -> Callable[[IngestState], str]:
    def route(state: IngestState) -> str:
        return "finish" if state.get("failure") else next_node

    return route
