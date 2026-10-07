"""One client for every task: routing, budgets, privacy, retries, fallback (spec §4)."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from tuppence.config.models import Budgets
from tuppence.core.clock import to_iso
from tuppence.core.household import HouseholdService
from tuppence.core.settings_store import SettingsStore
from tuppence.llm.budget import BreakerBoard, RunBudget, UsageLedger, estimate_tokens, priced_cost
from tuppence.llm.connections import Connection, ConnectionRegistry, ModelInfo
from tuppence.llm.jsonextract import extract_json, to_strict_schema
from tuppence.llm.pseudonymise import Pseudonymiser
from tuppence.llm.routing import TaskRouter, timeout_for
from tuppence.llm.types import (
    AllModelsBlocked,
    AllModelsFailed,
    BudgetExceeded,
    ChatRequest,
    ChatResponse,
    ContextTooLarge,
    LLMBadResponse,
    LLMError,
    LLMHTTPError,
    LLMTimeout,
    Message,
    NoticeRequired,
    ToolSpec,
    Usage,
)
from tuppence.net.client import LocalOnlyBlocked
from tuppence.net.privacy_log import PrivacyEvent, PrivacyLog

T = TypeVar("T", bound=BaseModel)
MAX_RETRIES = 2
BACKOFF = (1.0, 4.0)
MAX_RETRY_AFTER = 30.0


def _redact_value(value: Any, pseudo: Pseudonymiser) -> Any:
    if isinstance(value, str):
        return pseudo.redact(value)
    if isinstance(value, list):
        return [_redact_value(v, pseudo) for v in value]
    if isinstance(value, dict):
        return {k: _redact_value(v, pseudo) for k, v in value.items()}
    return value


def _problem(exc: ValueError | ValidationError) -> str:
    """What was wrong with a reply, in a form a model can act on."""
    if isinstance(exc, ValidationError):
        found = [
            f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['msg']}"
            for e in exc.errors(include_input=False)
        ]
        return "; ".join(found)[:300]
    return str(exc).splitlines()[0][:300] if str(exc) else "not valid JSON"


class ChatResult(ChatResponse):
    connection_id: str
    model_id: str
    redactions: int
    cost_gbp: float | None


class LLMClient:
    def __init__(
        self,
        *,
        connections: ConnectionRegistry,
        router: TaskRouter,
        usage: UsageLedger,
        settings: SettingsStore,
        household: HouseholdService,
        breakers: BreakerBoard,
        privacy_log: PrivacyLog,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.connections, self.router, self.usage = connections, router, usage
        self.privacy_log = privacy_log
        self._spend_lock = threading.Lock()
        self._reserved = 0.0  # £ projected for calls in flight
        self.settings, self.household, self.breakers, self.sleep = (
            settings,
            household,
            breakers,
            sleep,
        )

    def _pseudonymiser(self) -> Pseudonymiser:
        people = [
            (p.display_name, p.role) for p in self.household.list_people(include_retired=True)
        ]
        return Pseudonymiser(people, self.settings.get("privacy.hidden_names"))

    def new_run(self, budgets: Budgets) -> RunBudget:
        """A run budget limited by the agent manifest and the user's per-run £ cap."""
        return RunBudget.from_manifest(budgets, self.settings.get("llm.run_cap_gbp"))

    def _wait(self, delay: float, run: RunBudget | None) -> None:
        """Sleep before a retry or fallback, unless the run's time limit can't afford it."""
        if run is not None and delay >= run.remaining_seconds():
            raise BudgetExceeded(
                f"This run reached its time limit of {run.max_seconds:.0f} seconds."
            )
        self.sleep(delay)

    @staticmethod
    def _retry_after(exc: LLMError) -> float | None:
        wait = getattr(exc, "retry_after", None)
        if wait is not None and math.isfinite(wait) and wait >= 0:
            return min(MAX_RETRY_AFTER, float(wait))
        return None

    def _send(
        self,
        conn: Connection,
        task: str,
        req: ChatRequest,
        redactions: int,
        require_local: bool,
        run: RunBudget | None,
    ) -> ChatResponse:
        """One model, with up to two retries; the run's time limit bounds every attempt."""
        attempt = 0
        while True:
            timeout = timeout_for(task, conn.is_local)
            clamped = False
            if run is not None:
                run.check_time()
                left = max(run.remaining_seconds(), 1.0)
                clamped = left < timeout
                timeout = min(timeout, left)
            provider = self.connections.provider(
                conn.id,
                task=task,
                redactions=redactions,
                timeout=timeout,
                require_local=require_local,
            )
            try:
                return provider.chat(req)
            except LLMError as exc:
                if clamped and run is not None and isinstance(exc, LLMTimeout):
                    # Our own shortened timeout ran out: that's the run's limit, not a sick server.
                    raise BudgetExceeded(
                        f"This run reached its time limit of {run.max_seconds:.0f} seconds."
                    ) from exc
                if not exc.retryable or attempt >= MAX_RETRIES:
                    raise
                wait = self._retry_after(exc)
                self._wait(BACKOFF[attempt] if wait is None else wait, run)
                attempt += 1
            finally:
                provider.client.close()

    def chat(
        self,
        task: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "result",
        max_tokens: int = 4096,
        run: RunBudget | None = None,
        run_id: str | None = None,
    ) -> ChatResult:
        """Ask the task's models in order until one answers.

        `json_schema` is sent natively only to models that support it; `structured` always
        also puts the schema in the prompt, so other models still see it.
        A ValueError from an adapter is a bug in the caller's messages: it is re-raised,
        never retried and never passed on to the next model.
        """
        attempts: list[str] = []
        blocks: list[LocalOnlyBlocked] = []
        run_skips: list[str] = []
        pinned = self.router.is_pinned_local(task)
        # Read once: the guard enforces this decision even if the setting changes mid-call.
        require_local = pinned or bool(self.settings.get("privacy.local_only"))
        cooldown: dict[str, float] = {}
        for conn, model in self.router.chain_for(task):
            label = f"{conn.name} / {model.model_id}"
            if require_local and not conn.is_local:
                # Decided first: no key lookup, no cap or notice checks, just the block.
                block = self._log_block(conn, task, pinned)
                blocks.append(block)
                attempts.append(f"{label}: {block}")
                continue
            if not self.breakers.allow(conn.id):
                attempts.append(f"{label}: paused after repeated failures")
                continue
            if conn.needs_notice:
                notice = NoticeRequired(
                    f"Confirm what {conn.name} will see before Tuppence uses it (Settings › AI)."
                )
                attempts.append(f"{label}: {notice}")
                continue
            outcome = self._try_model(
                conn,
                model,
                task,
                messages,
                tools,
                json_schema,
                schema_name,
                max_tokens,
                run,
                run_id,
                require_local,
                cooldown,
                run_skips,
            )
            if isinstance(outcome, str):
                attempts.append(f"{label}: {outcome}")
                continue
            return outcome
        if blocks and len(blocks) == len(attempts):
            first = blocks[0]
            raise AllModelsBlocked(str(first), host=first.host, pinned=first.pinned)
        if run_skips and len(run_skips) == len(attempts):
            raise BudgetExceeded(run_skips[0])
        raise AllModelsFailed(attempts)

    def _log_block(self, conn: Connection, task: str, pinned: bool) -> LocalOnlyBlocked:
        """Record a cloud call that Local only (or a task pin) stopped before it was built."""
        url = urlparse(conn.base_url)
        host = url.hostname or conn.base_url
        note = "Task is set to local models only" if pinned else "Local only is on"
        self.privacy_log.record(
            PrivacyEvent(
                ts=to_iso(self.usage.clock()),
                purpose="llm",
                task=task,
                connection_id=conn.id,
                destination=host,
                method="POST",
                path=url.path or "/",
                bytes_out=0,
                bytes_in=0,
                status=None,
                redactions=0,
                outcome="blocked",
                note=note,
            )
        )
        return LocalOnlyBlocked(host, pinned=pinned)

    def _try_model(
        self,
        conn: Connection,
        model: ModelInfo,
        task: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
        json_schema: dict[str, Any] | None,
        schema_name: str,
        max_tokens: int,
        run: RunBudget | None,
        run_id: str | None,
        require_local: bool,
        cooldown: dict[str, float],
        run_skips: list[str],
    ) -> ChatResult | str:
        """A ChatResult, or the reason this model couldn't answer."""
        estimate = estimate_tokens(messages, tools)
        window = model.context_window
        room = window - estimate
        if estimate > window * 0.6 or room < 256:
            fits = max(0, min(int(window * 0.6), window - 256))
            return str(
                ContextTooLarge(
                    f"This is too much text for {model.model_id} (about {estimate:,} tokens; "
                    f"it fits {fits:,}). Choose a model with a bigger context window."
                )
            )
        max_tokens_eff = min(max_tokens, room, model.max_output_tokens or max_tokens)
        rate = self.settings.get("llm.usd_to_gbp")
        projected = 0.0
        if not conn.is_local:
            projected, _ = priced_cost(
                model, Usage(input_tokens=estimate, output_tokens=max_tokens_eff), rate
            )
        cap = self.settings.get("llm.monthly_cap_gbp")
        now = self.usage.clock()
        with self._spend_lock:
            spent = self.usage.month_spend_gbp(now.year, now.month) + self._reserved
            if projected > 0 and spent + projected > cap:
                return str(
                    BudgetExceeded(f"This month's AI spending cap (£{cap:.2f}) would be exceeded.")
                )
            self._reserved += projected
        try:
            if run is not None:
                run.check_limits()  # calls and time end the run; a cap only skips this model
                reason = run.over_cap(estimate + max_tokens_eff, projected)
                if reason:
                    run_skips.append(reason)
                    return reason
            use_pseudo = not conn.is_local and self.settings.get("privacy.pseudonymise")
            pseudo = self._pseudonymiser() if use_pseudo else None
            sent = [self._redacted(m, pseudo) for m in messages] if pseudo else list(messages)
            req = ChatRequest(
                model=model.model_id,
                messages=sent,
                tools=list(tools),
                json_schema=json_schema if model.supports_json_schema else None,
                schema_name=schema_name,
                max_tokens=max_tokens_eff,
            )
            try:
                wait = cooldown.pop(conn.id, None)
                if wait:
                    self._wait(wait, run)  # the provider asked us to slow down
                resp = self._send(
                    conn, task, req, pseudo.count if pseudo else 0, require_local, run
                )
            except LocalOnlyBlocked as exc:
                return str(exc)
            except BudgetExceeded:
                raise
            except LLMError as exc:
                # Only a connection that looks unhealthy counts towards the breaker; a
                # refusal or a malformed reply means the server answered.
                if exc.retryable:
                    self.breakers.failure(conn.id)
                else:
                    self.breakers.success(conn.id)
                if isinstance(exc, LLMHTTPError) and exc.status == 429:
                    cooldown[conn.id] = self._retry_after(exc) or BACKOFF[-1]
                self.usage.record(
                    task,
                    conn.id,
                    model.model_id,
                    Usage(),
                    None,
                    ok=False,
                    error=str(exc),
                    run_id=run_id,
                )
                return str(exc)
            self.breakers.success(conn.id)
            usage, estimated = resp.usage, False
            if usage.input_tokens == 0 or usage.output_tokens == 0:
                # The provider left out a count: fill it in from the text instead.
                out_chars = len(resp.text) + sum(
                    len(json.dumps(c.arguments)) + len(c.name) for c in resp.tool_calls
                )
                usage = Usage(
                    input_tokens=usage.input_tokens or estimate,
                    output_tokens=usage.output_tokens or math.ceil(out_chars / 4),
                )
                estimated = usage != resp.usage
            if conn.is_local:
                cost = 0.0
            else:
                cost, fallback_price = priced_cost(model, usage, rate)
                estimated = estimated or fallback_price
            self.usage.record(
                task,
                conn.id,
                model.model_id,
                usage,
                cost,
                ok=True,
                run_id=run_id,
                estimated=estimated,
            )
            if run is not None:
                run.record(usage.input_tokens + usage.output_tokens, cost)
        finally:
            with self._spend_lock:
                self._reserved -= projected
        text = pseudo.restore(resp.text) if pseudo else resp.text
        calls = resp.tool_calls
        if pseudo:
            calls = [
                c.model_copy(update={"arguments": pseudo.restore_value(c.arguments)}) for c in calls
            ]
        return ChatResult(
            text=text,
            tool_calls=calls,
            usage=usage,
            model=resp.model,
            finish_reason=resp.finish_reason,
            connection_id=conn.id,
            model_id=model.model_id,
            redactions=pseudo.count if pseudo else 0,
            cost_gbp=cost,
        )

    @staticmethod
    def _redacted(message: Message, pseudo: Pseudonymiser) -> Message:
        calls = [
            c.model_copy(update={"arguments": _redact_value(c.arguments, pseudo)})
            for c in message.tool_calls
        ]
        return message.model_copy(
            update={"content": pseudo.redact(message.content), "tool_calls": calls}
        )

    def structured(
        self,
        task: str,
        messages: Sequence[Message],
        schema: type[T],
        *,
        max_tokens: int = 4096,
        run: RunBudget | None = None,
        run_id: str | None = None,
    ) -> T:
        strict = to_strict_schema(schema.model_json_schema())
        instruction = Message(
            role="system",
            content=(
                "Reply with only a JSON value that matches this JSON Schema. "
                "No prose, no code fences.\n" + json.dumps(strict)
            ),
        )
        convo = [instruction, *messages]
        first = self.chat(
            task,
            convo,
            json_schema=strict,
            schema_name=schema.__name__,
            max_tokens=max_tokens,
            run=run,
            run_id=run_id,
        )
        try:
            return schema.model_validate(extract_json(first.text))
        except (ValueError, ValidationError) as exc:
            problem = _problem(exc)
        repair = [
            *convo,
            Message(role="assistant", content=first.text[:4000]),
            Message(
                role="user",
                content=f"That wasn't valid: {problem}. Reply with only the corrected JSON.",
            ),
        ]
        second = self.chat(
            task,
            repair,
            json_schema=strict,
            schema_name=schema.__name__,
            max_tokens=max_tokens,
            run=run,
            run_id=run_id,
        )
        try:
            return schema.model_validate(extract_json(second.text))
        except (ValueError, ValidationError) as exc:
            raise LLMBadResponse(
                f"The model's reply didn't match the expected format: {_problem(exc)[:200]}"
            ) from exc
