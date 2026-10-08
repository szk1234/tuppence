import pytest

from tuppence.agents.runtime import AnalysisContext, LayeredBudget
from tuppence.llm.budget import RunBudget
from tuppence.llm.types import BudgetExceeded


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def budget(*, calls=5, tokens=1000, gbp=1.0, seconds=60.0, clock=None) -> RunBudget:
    if clock is None:
        return RunBudget(max_calls=calls, max_tokens=tokens, max_gbp=gbp, max_seconds=seconds)
    return RunBudget(
        max_calls=calls, max_tokens=tokens, max_gbp=gbp, max_seconds=seconds, monotonic=clock
    )


def test_a_layered_budget_has_the_whole_run_budget_surface():
    """Everything LLMClient uses on a RunBudget, applied to every layer (G4)."""
    clock = Clock()
    own = budget(seconds=100, clock=clock)
    run = budget(seconds=50, clock=clock)
    layered = LayeredBudget(own, run)
    assert layered.max_seconds == 50
    clock.now += 20
    assert layered.remaining_seconds() == 30
    layered.check_time()
    layered.check_limits()
    assert layered.over_cap(10, 0.0) is None
    layered.check(10)  # the £ projection defaults to nothing
    layered.check(10, 0.5)
    layered.start_call()
    layered.record(120, 0.25)
    assert (layered.calls, layered.tokens, layered.gbp) == (1, 120, 0.25)
    assert (run.calls, run.tokens, run.gbp) == (1, 120, 0.25)


def test_no_layer_counts_a_call_another_layer_refuses():
    own, run = budget(calls=5), budget(calls=1)
    run.calls = 1  # the whole run has used its one call
    layered = LayeredBudget(own, run)
    with pytest.raises(BudgetExceeded, match="limit of 1 AI calls"):
        layered.start_call()
    assert (own.calls, run.calls) == (0, 1)
    with pytest.raises(BudgetExceeded):
        layered.check_limits()


def test_the_tightest_layer_sets_the_caps_and_the_time():
    clock = Clock()
    own = budget(tokens=10_000, gbp=5.0, seconds=600, clock=clock)
    run = budget(tokens=100, gbp=0.10, seconds=30, clock=clock)
    layered = LayeredBudget(own, run)
    assert layered.over_cap(200, 0.0) == "This run reached its limit of 100 tokens."
    assert "£0.10 spending limit" in (layered.over_cap(10, 0.5) or "")
    with pytest.raises(BudgetExceeded, match="100 tokens"):
        layered.check(200)
    clock.now += 31
    assert layered.remaining_seconds() < 0
    with pytest.raises(BudgetExceeded, match="time limit of 30 seconds"):
        layered.check_time()


def test_the_counters_are_the_specialists_own():
    own, run = budget(), budget()
    run.max_calls = 20
    run.calls, run.tokens, run.gbp = 7, 700, 0.7  # other specialists' use of the run
    layered = LayeredBudget(own, run)
    layered.start_call()
    layered.record(50, None)
    assert (layered.calls, layered.tokens, layered.gbp) == (1, 50, 0.0)
    assert (run.calls, run.tokens) == (8, 750)


def test_the_analysis_context_hands_each_specialist_its_budget():
    run = budget(calls=10)
    mine = LayeredBudget(budget(calls=2), run)
    theirs = LayeredBudget(budget(calls=3), run)
    context = AnalysisContext(run_id="run_x", budgets={"categoriser": mine, "commitments": theirs})
    assert context.budget("categoriser") is mine and context.budget("commitments") is theirs
    with pytest.raises(KeyError):
        context.budget("coach")
