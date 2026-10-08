import pytest
from pydantic import BaseModel

from tuppence.llm.budget import estimate_tokens
from tuppence.llm.context import (
    ContextBudget,
    capacity,
    max_tokens_for,
    plan_batches,
    structured_overhead,
    text_tokens,
)
from tuppence.llm.types import ContextTooLarge, Message


class Reply(BaseModel):
    ref: str
    category_id: str


def test_budget_reserves_output_and_caps_input():
    b = ContextBudget(4096)
    assert (b.input_tokens, b.output_tokens) == (2457, 1024)
    assert max_tokens_for(3, per_item=60, budget=b) == 330
    assert max_tokens_for(100, per_item=60, budget=b) == 1024


def test_overhead_matches_what_the_client_adds():
    assert structured_overhead(Reply) > text_tokens('{"ref"')
    assert text_tokens("abcd") == 1 and text_tokens("abcde") == 2


def test_batches_fit_and_shared_lines_count_once_per_batch():
    rows = [("m1", 30)] * 10 + [("m2", 30)] * 10
    batches = plan_batches(
        rows,
        budget=ContextBudget(1000),
        fixed_tokens=300,
        item_tokens=lambda r: r[1],
        output_tokens_per_item=10,
        shared=lambda r: (r[0], 50),
    )
    # 600 - 300 = 300 tokens of room: a merchant's memory (50) once per batch, 30 per row.
    assert [len(b) for b in batches] == [8, 6, 6]  # 50+8*30 | 50+2*30+50+4*30 | 50+6*30
    assert sum(len(b) for b in batches) == 20


def test_output_room_and_max_items_also_split():
    rows = list(range(10))
    assert [
        len(b)
        for b in plan_batches(
            rows,
            budget=ContextBudget(100_000),
            fixed_tokens=0,
            item_tokens=lambda _: 1,
            output_tokens_per_item=1,
            max_items=4,
        )
    ] == [4, 4, 2]
    small_out = plan_batches(
        rows,
        budget=ContextBudget(1000),
        fixed_tokens=0,
        item_tokens=lambda _: 1,
        output_tokens_per_item=40,
    )
    assert [len(b) for b in small_out] == [2, 2, 2, 2, 2]  # (250 - 150) // 40 = 2 per batch


def test_a_model_too_small_says_so():
    with pytest.raises(ContextTooLarge, match="too small"):
        plan_batches(
            [1],
            budget=ContextBudget(1000),
            fixed_tokens=590,
            item_tokens=lambda _: 20,
            output_tokens_per_item=10,
        )
    assert (
        capacity(
            ContextBudget(4096), fixed_tokens=1500, tokens_per_item=40, output_tokens_per_item=60
        )
        == 14
    )
    assert (
        capacity(
            ContextBudget(1000), fixed_tokens=700, tokens_per_item=40, output_tokens_per_item=60
        )
        == 0
    )


def test_a_planned_batch_passes_the_clients_check():
    """The client refuses inputs over 60% of the window, or input + max_tokens over it."""
    window = 4096
    budget = ContextBudget(window)
    system = Message(role="system", content="x" * 2400)
    rows = [f'{{"ref": "T{i}", "text": "GREENBASKET STORES {i:04d}"}}' for i in range(200)]
    fixed = structured_overhead(Reply) + text_tokens(system.content) + 2 * 8
    for batch in plan_batches(
        rows,
        budget=budget,
        fixed_tokens=fixed,
        item_tokens=lambda r: text_tokens(r) + 1,
        output_tokens_per_item=40,
    ):
        user = Message(role="user", content="\n".join(batch))
        instruction = Message(role="system", content="y" * (4 * (structured_overhead(Reply) - 8)))
        estimate = estimate_tokens([instruction, system, user])
        out = max_tokens_for(len(batch), per_item=40, budget=budget)
        assert estimate <= window * 0.6 and estimate + out <= window
