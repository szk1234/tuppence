import pytest

from tuppence.config.models import Budgets
from tuppence.llm.budget import BreakerBoard, RunBudget, cost_gbp, estimate_tokens
from tuppence.llm.connections import ModelInfo
from tuppence.llm.types import BudgetExceeded, Message, Usage


def model(**kw):
    base = dict(
        connection_id="c",
        model_id="m",
        display_name=None,
        context_window=8000,
        max_output_tokens=None,
        supports_tools=False,
        supports_json_schema=False,
        supports_vision=False,
        price_in_usd_per_mtok=2.0,
        price_out_usd_per_mtok=8.0,
        source="catalogue",
    )
    base.update(kw)
    return ModelInfo(**base)


def test_estimate_tokens():
    assert estimate_tokens([Message(role="user", content="x" * 400)]) == 108


def test_cost():
    assert cost_gbp(
        model(), Usage(input_tokens=1_000_000, output_tokens=500_000), 0.75
    ) == pytest.approx(4.5)
    assert cost_gbp(model(price_in_usd_per_mtok=None), Usage(input_tokens=10), 0.75) is None


def test_run_budget_caps():
    t = [0.0]
    b = RunBudget(
        max_calls=2, max_tokens=1000, max_gbp=0.10, max_seconds=30, monotonic=lambda: t[0]
    )
    b.check(400)
    b.record(400, 0.05)
    with pytest.raises(BudgetExceeded, match="tokens"):
        b.check(700)
    b.record(100, 0.06)
    with pytest.raises(BudgetExceeded, match="calls"):
        b.check(1)
    b2 = RunBudget(
        max_calls=10, max_tokens=10_000, max_gbp=1, max_seconds=30, monotonic=lambda: t[0]
    )
    t[0] = 31
    with pytest.raises(BudgetExceeded, match="time"):
        b2.check(1)


def test_breaker_opens_and_resets():
    t = [0.0]
    br = BreakerBoard(threshold=3, reset_s=60, monotonic=lambda: t[0])
    for _ in range(3):
        assert br.allow("c")
        br.failure("c")
    assert not br.allow("c")
    t[0] = 61
    assert br.allow("c")  # half-open trial
    br.success("c")
    assert br.allow("c")


def test_from_manifest_takes_the_lower_gbp_cap():
    budgets = Budgets(max_llm_calls=5, max_tokens=1000, max_gbp=2.0, max_seconds=30)
    assert RunBudget.from_manifest(budgets).max_gbp == 2.0
    assert RunBudget.from_manifest(budgets, run_cap_gbp=0.5).max_gbp == 0.5
    assert RunBudget.from_manifest(budgets, run_cap_gbp=9).max_gbp == 2.0
