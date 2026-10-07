"""Token estimates, prices, per-run budgets, monthly spend and circuit breakers (spec §4.5)."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from tuppence.config.models import Budgets
from tuppence.core.clock import to_iso, utcnow
from tuppence.core.db import Database
from tuppence.llm.connections import ModelInfo
from tuppence.llm.types import BudgetExceeded, Message, ToolSpec, Usage


def estimate_tokens(messages: Iterable[Message], tools: Iterable[ToolSpec] = ()) -> int:
    chars = 0
    count = 0
    for m in messages:
        count += 1
        chars += len(m.content) + sum(
            len(json.dumps(c.arguments)) + len(c.name) for c in m.tool_calls
        )
    chars += sum(len(json.dumps(t.model_dump())) for t in tools)
    return math.ceil(chars / 4) + 8 * count


# Prices Tuppence uses for a cloud model it has no price for, so spending caps never treat
# unknown spend as free. Deliberately on the high side (a mid-range frontier model); the user
# can set the real price on the model. Rows priced this way are marked `estimated`.
FALLBACK_PRICE_IN_USD_PER_MTOK = 5.0
FALLBACK_PRICE_OUT_USD_PER_MTOK = 15.0


def priced_cost(model: ModelInfo, usage: Usage, usd_to_gbp: float) -> tuple[float, bool]:
    """Cost in pounds and whether a fallback price was used for an unknown price."""
    price_in, price_out = model.price_in_usd_per_mtok, model.price_out_usd_per_mtok
    fallback = price_in is None or price_out is None
    if price_in is None:
        price_in = FALLBACK_PRICE_IN_USD_PER_MTOK
    if price_out is None:
        price_out = FALLBACK_PRICE_OUT_USD_PER_MTOK
    usd = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
    return round(usd * usd_to_gbp, 6), fallback


def cost_gbp(model: ModelInfo, usage: Usage, usd_to_gbp: float) -> float | None:
    if model.price_in_usd_per_mtok is None or model.price_out_usd_per_mtok is None:
        return None
    usd = (
        usage.input_tokens * model.price_in_usd_per_mtok
        + usage.output_tokens * model.price_out_usd_per_mtok
    ) / 1e6
    return round(usd * usd_to_gbp, 6)


@dataclass
class RunBudget:
    max_calls: int
    max_tokens: int
    max_gbp: float
    max_seconds: float
    monotonic: Callable[[], float] = time.monotonic
    calls: int = 0
    tokens: int = 0
    gbp: float = 0.0
    started: float = field(default=-1.0)

    def __post_init__(self) -> None:
        if self.started < 0:
            self.started = self.monotonic()

    @classmethod
    def from_manifest(cls, budgets: Budgets, run_cap_gbp: float) -> RunBudget:
        """The agent's own budget, with its £ limit lowered to the user's per-run cap if smaller."""
        gbp = min(budgets.max_gbp, run_cap_gbp)
        return cls(budgets.max_llm_calls, budgets.max_tokens, gbp, budgets.max_seconds)

    def remaining_seconds(self) -> float:
        return self.max_seconds - (self.monotonic() - self.started)

    def check_time(self) -> None:
        if self.remaining_seconds() <= 0:
            raise BudgetExceeded(
                f"This run reached its time limit of {self.max_seconds:.0f} seconds."
            )

    def check(self, estimated_tokens: int, projected_gbp: float = 0.0) -> None:
        if self.calls + 1 > self.max_calls:
            raise BudgetExceeded(f"This run reached its limit of {self.max_calls} AI calls.")
        if self.tokens + estimated_tokens > self.max_tokens:
            raise BudgetExceeded(f"This run reached its limit of {self.max_tokens:,} tokens.")
        if self.gbp + projected_gbp > self.max_gbp:
            raise BudgetExceeded(
                f"This call could take this run past its £{self.max_gbp:.2f} spending limit."
            )
        if self.monotonic() - self.started > self.max_seconds:
            raise BudgetExceeded(
                f"This run reached its time limit of {self.max_seconds:.0f} seconds."
            )

    def record(self, tokens: int, gbp: float | None) -> None:
        self.calls += 1
        self.tokens += tokens
        self.gbp += gbp or 0.0


class UsageLedger:
    def __init__(self, db: Database, clock: Callable[[], datetime] = utcnow) -> None:
        self.db, self.clock = db, clock

    def record(
        self,
        task: str,
        connection_id: str | None,
        model_id: str,
        usage: Usage,
        cost: float | None,
        ok: bool,
        error: str | None = None,
        run_id: str | None = None,
        estimated: bool = False,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO llm_usage (ts, run_id, task, connection_id, model_id,"
                " input_tokens, output_tokens, cost_gbp, ok, error, estimated)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    to_iso(self.clock()),
                    run_id,
                    task,
                    connection_id,
                    model_id,
                    usage.input_tokens,
                    usage.output_tokens,
                    cost,
                    int(ok),
                    (error or "")[:500] or None,
                    int(estimated),
                ],
            )

    def _month_rows(self, year: int, month: int) -> list[Any]:
        start = date(year, month, 1)
        end = date(year + (month == 12), month % 12 + 1, 1)
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT * FROM llm_usage WHERE ts >= ? AND ts < ?",
                [start.isoformat(), end.isoformat()],
            ).fetchall()

    def month_spend_gbp(self, year: int, month: int) -> float:
        return round(sum(r["cost_gbp"] or 0.0 for r in self._month_rows(year, month)), 6)

    def summary(self, year: int, month: int) -> dict[str, Any]:
        rows = self._month_rows(year, month)
        out: dict[str, Any] = {
            "total_gbp": 0.0,
            "calls": sum(1 for r in rows if r["ok"]),
            "failed_calls": sum(1 for r in rows if not r["ok"]),
            "estimated_calls": sum(1 for r in rows if r["estimated"]),
            "by_task": {},
            "by_model": {},
            "by_day": {},
        }
        for r in rows:
            gbp = r["cost_gbp"] or 0.0
            tokens = r["input_tokens"] + r["output_tokens"]
            out["total_gbp"] += gbp
            for bucket, key in (
                ("by_task", r["task"]),
                ("by_model", r["model_id"]),
                ("by_day", r["ts"][:10]),
            ):
                agg = out[bucket].setdefault(
                    key, {"calls": 0, "failed_calls": 0, "tokens": 0, "gbp": 0.0}
                )
                agg["failed_calls" if not r["ok"] else "calls"] += 1
                agg["tokens"] += tokens
                agg["gbp"] += gbp
        out["total_gbp"] = round(out["total_gbp"], 4)
        return out


class BreakerBoard:
    """Opens a connection after consecutive health failures (thread-safe)."""

    def __init__(
        self,
        threshold: int = 3,
        reset_s: float = 60.0,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold, self.reset_s, self.monotonic = threshold, reset_s, monotonic
        self.failures: dict[str, int] = {}
        self.opened_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            opened = self.opened_at.get(key)
            if opened is None:
                return True
            if self.monotonic() - opened >= self.reset_s:
                self.opened_at.pop(key, None)
                self.failures[key] = self.threshold - 1  # half-open: one more failure re-opens
                return True
            return False

    def success(self, key: str) -> None:
        with self._lock:
            self.failures.pop(key, None)
            self.opened_at.pop(key, None)

    def failure(self, key: str) -> None:
        with self._lock:
            self.failures[key] = self.failures.get(key, 0) + 1
            if self.failures[key] >= self.threshold:
                self.opened_at[key] = self.monotonic()
