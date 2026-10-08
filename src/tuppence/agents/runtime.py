"""What every analysis specialist shares while a run is going (spec §8.3, §10.1)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from tuppence.llm.types import BudgetExceeded, Message


class BudgetLayer(Protocol):
    """What the LLM client uses on a run budget (`RunBudget` has all of it)."""

    @property
    def max_seconds(self) -> float: ...
    @property
    def calls(self) -> int: ...
    @property
    def tokens(self) -> int: ...
    @property
    def gbp(self) -> float: ...
    def remaining_seconds(self) -> float: ...
    def check_time(self) -> None: ...
    def check_limits(self) -> None: ...
    def over_cap(self, estimated_tokens: int, projected_gbp: float) -> str | None: ...
    def start_call(self) -> None: ...
    def record(self, tokens: int, gbp: float | None) -> None: ...


class StructuredLLM(Protocol):
    def structured(
        self,
        task: str,
        messages: Sequence[Message],
        schema: type[Any],
        *,
        max_tokens: int = 4096,
        run: Any = None,
        run_id: str | None = None,
    ) -> Any: ...


class LayeredBudget:
    """A specialist's own caps (its manifest) and the whole run's caps, checked together.

    It has the whole surface `LLMClient` uses on a `RunBudget`, applied to every layer: the
    time left is the least any layer has, a call or token or £ cap of any layer stops a call,
    and an answered call is recorded on every layer. The first layer is the specialist's own:
    its counters are the ones reported."""

    def __init__(self, own: BudgetLayer, *others: BudgetLayer) -> None:
        self.own = own
        self.parts: tuple[BudgetLayer, ...] = (own, *others)

    @property
    def max_seconds(self) -> float:
        return min(part.max_seconds for part in self.parts)

    def remaining_seconds(self) -> float:
        return min(part.remaining_seconds() for part in self.parts)

    def check_time(self) -> None:
        for part in self.parts:
            part.check_time()

    def check_limits(self) -> None:
        """Limits no other model can help with (calls and time), on every layer."""
        for part in self.parts:
            part.check_limits()

    def over_cap(self, estimated_tokens: int, projected_gbp: float) -> str | None:
        """Why this call doesn't fit some layer's token or £ cap, or None."""
        for part in self.parts:
            reason = part.over_cap(estimated_tokens, projected_gbp)
            if reason:
                return reason
        return None

    def check(self, estimated_tokens: int, projected_gbp: float = 0.0) -> None:
        self.check_limits()
        reason = self.over_cap(estimated_tokens, projected_gbp)
        if reason:
            raise BudgetExceeded(reason)

    def start_call(self) -> None:
        """Count one HTTP attempt on every layer. Every layer is checked first, so no layer
        counts a call that another refuses."""
        self.check_limits()
        for part in self.parts:
            part.start_call()

    def record(self, tokens: int, gbp: float | None) -> None:
        for part in self.parts:
            part.record(tokens, gbp)

    @property
    def calls(self) -> int:
        return int(self.own.calls)

    @property
    def tokens(self) -> int:
        return int(self.own.tokens)

    @property
    def gbp(self) -> float:
        return float(self.own.gbp)


@dataclass
class AnalysisContext:
    """LangGraph run context: never checkpointed, rebuilt for every job attempt."""

    run_id: str
    budgets: dict[str, LayeredBudget] = field(default_factory=dict)  # by specialist name

    def budget(self, name: str) -> LayeredBudget:
        return self.budgets[name]
