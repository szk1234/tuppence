"""One client for every task: routing, budgets, privacy, retries, fallback (spec §4)."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from tuppence.config.models import Budgets
from tuppence.core.clock import utcnow
from tuppence.core.household import HouseholdService
from tuppence.core.settings_store import SettingsStore
from tuppence.llm.budget import BreakerBoard, RunBudget, UsageLedger, cost_gbp, estimate_tokens
from tuppence.llm.connections import ConnectionRegistry
from tuppence.llm.jsonextract import extract_json, to_strict_schema
from tuppence.llm.pseudonymise import Pseudonymiser
from tuppence.llm.routing import TaskRouter, timeout_for
from tuppence.llm.types import (
    AllModelsFailed,
    BudgetExceeded,
    ChatRequest,
    ChatResponse,
    ContextTooLarge,
    LLMBadResponse,
    LLMError,
    Message,
    NoticeRequired,
    ToolSpec,
    Usage,
)
from tuppence.net.client import LocalOnlyBlocked

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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.connections, self.router, self.usage = connections, router, usage
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

    def _call_with_retries(self, provider: Any, req: ChatRequest) -> ChatResponse:
        attempt = 0
        while True:
            try:
                return provider.chat(req)
            except LLMError as exc:
                if not exc.retryable or attempt >= MAX_RETRIES:
                    raise
                wait = getattr(exc, "retry_after", None)
                if wait is not None and math.isfinite(wait) and wait >= 0:
                    delay = min(MAX_RETRY_AFTER, float(wait))
                else:
                    delay = BACKOFF[attempt]
                self.sleep(delay)
                attempt += 1

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
        for conn, model in self.router.chain_for(task):
            label = f"{conn.name} / {model.model_id}"
            if not self.breakers.allow(conn.id):
                attempts.append(f"{label}: paused after repeated failures")
                continue
            if conn.needs_notice:
                notice = NoticeRequired(
                    f"Confirm what {conn.name} will see before Tuppence uses it (Settings › AI)."
                )
                attempts.append(f"{label}: {notice}")
                continue
            estimate = estimate_tokens(messages, tools)
            window = model.context_window
            room = window - estimate
            if estimate > window * 0.6 or room < 256:
                fits = max(0, min(int(window * 0.6), window - 256))
                too_big = ContextTooLarge(
                    f"This is too much text for {model.model_id} (about {estimate:,} tokens; "
                    f"it fits {fits:,}). Choose a model with a bigger context window."
                )
                attempts.append(f"{label}: {too_big}")
                continue
            max_tokens_eff = min(max_tokens, room, model.max_output_tokens or max_tokens)
            rate = self.settings.get("llm.usd_to_gbp")
            projected = cost_gbp(
                model, Usage(input_tokens=estimate, output_tokens=max_tokens_eff), rate
            )
            cap = self.settings.get("llm.monthly_cap_gbp")
            now = utcnow()
            if projected and self.usage.month_spend_gbp(now.year, now.month) + projected > cap:
                capped = BudgetExceeded(
                    f"This month's AI spending cap (£{cap:.2f}) would be exceeded."
                )
                attempts.append(f"{label}: {capped}")
                continue
            if run is not None:
                run.check(estimate + max_tokens_eff)
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
            provider = self.connections.provider(
                conn.id,
                task=task,
                redactions=pseudo.count if pseudo else 0,
                timeout=timeout_for(task, conn.is_local),
            )
            try:
                resp = self._call_with_retries(provider, req)
            except LocalOnlyBlocked as exc:
                attempts.append(f"{label}: {exc}")
                continue
            except LLMError as exc:
                self.breakers.failure(conn.id)
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
                attempts.append(f"{label}: {exc}")
                continue
            finally:
                provider.client.close()
            self.breakers.success(conn.id)
            cost = 0.0 if conn.is_local else cost_gbp(model, resp.usage, rate)
            self.usage.record(
                task, conn.id, model.model_id, resp.usage, cost, ok=True, run_id=run_id
            )
            if run is not None:
                run.record(resp.usage.input_tokens + resp.usage.output_tokens, cost)
            text = pseudo.restore(resp.text) if pseudo else resp.text
            calls = resp.tool_calls
            if pseudo:
                calls = [
                    c.model_copy(update={"arguments": pseudo.restore_value(c.arguments)})
                    for c in calls
                ]
            return ChatResult(
                text=text,
                tool_calls=calls,
                usage=resp.usage,
                model=resp.model,
                finish_reason=resp.finish_reason,
                connection_id=conn.id,
                model_id=model.model_id,
                redactions=pseudo.count if pseudo else 0,
                cost_gbp=cost,
            )
        raise AllModelsFailed(attempts)

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
        )
        try:
            return schema.model_validate(extract_json(first.text))
        except (ValueError, ValidationError) as exc:
            problem = str(exc).splitlines()[0][:300]
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
        )
        try:
            return schema.model_validate(extract_json(second.text))
        except (ValueError, ValidationError) as exc:
            raise LLMBadResponse(
                "The model's reply didn't match the expected format: "
                f"{str(exc).splitlines()[0][:200]}"
            ) from exc
