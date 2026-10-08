"""Context sizing (spec §10.3): fit each AI call to the model's context window.

A `ContextBudget` reserves 25% of the window for the reply and caps the prompt at 60%.
`plan_batches()` fills each batch with as many items as fit after the fixed parts of the
prompt, counting shared lines (such as one merchant's memory) once per batch. Token
counts use the same rule as the LLM client (characters ÷ 4, plus 8 per message), so a
planned batch never trips the client's ContextTooLarge check.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from tuppence.llm.jsonextract import to_strict_schema
from tuppence.llm.types import ContextTooLarge

MESSAGE_TOKENS = 8  # what estimate_tokens() adds per message
STRUCTURED_INSTRUCTION = (
    "Reply with only a JSON value that matches this JSON Schema. No prose, no code fences.\n"
)


def text_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def structured_overhead(schema: type[BaseModel]) -> int:
    """Tokens LLMClient.structured() adds: its instruction message with the strict schema."""
    strict = to_strict_schema(schema.model_json_schema())
    return text_tokens(STRUCTURED_INSTRUCTION + json.dumps(strict)) + MESSAGE_TOKENS


@dataclass(frozen=True)
class ContextBudget:
    context_window: int
    input_share: float = 0.60
    output_share: float = 0.25

    @property
    def input_tokens(self) -> int:
        return int(self.context_window * self.input_share)

    @property
    def output_tokens(self) -> int:
        return int(self.context_window * self.output_share)


def plan_batches[T](
    items: Sequence[T],
    *,
    budget: ContextBudget,
    fixed_tokens: int,
    item_tokens: Callable[[T], int],
    output_tokens_per_item: int,
    output_base_tokens: int = 150,
    shared: Callable[[T], tuple[str, int] | None] | None = None,
    max_items: int = 60,
) -> list[list[T]]:
    """Split `items` into batches that fit `budget`, keeping their order.

    `fixed_tokens` is everything sent with every batch (instructions, schema, the category
    tree). `shared(item)` names a line several items may share (a merchant's memory) and
    its cost, counted once per batch. Raises ContextTooLarge when a single item can't fit,
    which means the model is too small for this job."""
    room_in = budget.input_tokens - fixed_tokens
    room_out = budget.output_tokens - output_base_tokens
    batches: list[list[T]] = []
    current: list[T] = []
    used_in, seen = 0, set[str]()
    for item in items:
        cost = item_tokens(item)
        extra = shared(item) if shared else None
        if extra is not None and extra[0] not in seen:
            cost += extra[1]
        fits_in = used_in + cost <= room_in
        fits_out = (len(current) + 1) * output_tokens_per_item <= room_out
        if current and (not fits_in or not fits_out or len(current) >= max_items):
            batches.append(current)
            current, used_in, seen = [], 0, set()
            cost = item_tokens(item) + (extra[1] if extra is not None else 0)
        if not current and (cost > room_in or output_tokens_per_item > room_out):
            raise ContextTooLarge(
                f"This AI model's context window ({budget.context_window:,} tokens) is too small"
                " for this job. Choose a model with a bigger context window."
            )
        current.append(item)
        used_in += cost
        if extra is not None:
            seen.add(extra[0])
    if current:
        batches.append(current)
    return batches


def capacity(
    budget: ContextBudget,
    *,
    fixed_tokens: int,
    tokens_per_item: int,
    output_tokens_per_item: int,
    output_base_tokens: int = 150,
) -> int:
    """How many typical items fit in one call (0 when not even one does)."""
    by_input = (budget.input_tokens - fixed_tokens) // max(1, tokens_per_item)
    by_output = (budget.output_tokens - output_base_tokens) // max(1, output_tokens_per_item)
    return max(0, min(by_input, by_output))


def max_tokens_for(
    batch_size: int, *, per_item: int, base: int = 150, budget: ContextBudget
) -> int:
    """The reply allowance for a batch: what it needs, never more than the 25% reserve."""
    return min(budget.output_tokens, base + per_item * batch_size)
