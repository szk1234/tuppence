"""The Categoriser through the real LLM client: its budgets, its usage ledger, the problems it
raises (G5) and its JSON repair turn. Every request goes to a scripted server; no network."""

from datetime import date

import pytest

from tuppence.agents import categoriser
from tuppence.core.secrets import SecretUnreadable

D = date(2026, 10, 1)


def test_the_client_runs_on_the_specialists_layered_budget(cenv):
    env = cenv.agent
    cenv.local_model()
    ids = [env.add_txn(D, -4218, "GREENBASKET STORES 0873"), env.add_txn(D, -340, "LITTLE CAFE")]
    counts = env.categorise(ids)
    assert (counts["llm"], counts["stopped"]) == (2, "")
    assert len(cenv.chats()) == 1
    budget = env.last_context.budget("categoriser")
    assert (budget.calls, budget.tokens) == (1, 150)  # the scripted server's usage
    with cenv.services.db.connection() as conn:
        usage = [tuple(r) for r in conn.execute("SELECT task, run_id, ok FROM llm_usage")]
    assert usage == [("categorise", "run_test", 1)]  # grouped by the analysis run


def _unreadable(connection_id):
    raise SecretUnreadable("The saved API key can't be read. Enter it again in Settings › AI.")


@pytest.mark.parametrize(
    "problem, text",
    [
        ("notice", "Confirm what OpenAI will see before Tuppence uses it"),
        ("local only", "Local only is on, so Tuppence didn't contact 127.0.0.1"),
        ("unreadable key", "The saved API key can't be read"),
    ],
)
def test_an_ai_problem_leaves_rows_awaiting_ai_with_the_reason(cenv, monkeypatch, problem, text):
    """G5: an unacknowledged cloud notice, Local only with a cloud model and a key that can't
    be read each stop the Categoriser cleanly (the run ends partial, never failed)."""
    env, services = cenv.agent, cenv.services
    cenv.cloud_model(acknowledge=problem != "notice")
    if problem == "local only":
        services.settings.set("privacy.local_only", True, expected_version=0)
    if problem == "unreadable key":
        monkeypatch.setattr(services.connections, "api_key", _unreadable)
    ids = [env.add_txn(D, -4218, "GREENBASKET STORES 0873"), env.add_txn(D, -340, "LITTLE CAFE")]
    counts = env.categorise(ids)
    assert (counts["stopped"], counts["awaiting_ai"], counts["llm"]) == ("awaiting_ai", 2, 0)
    assert text in counts["ai_problem"]
    assert [env.understanding.get(i).waiting for i in ids] == ["awaiting_ai", "awaiting_ai"]
    assert cenv.chats() == []  # nothing left the machine


def _garbage(body: dict) -> str:
    """Not JSON, and as long as the reply may be."""
    return "x" * (body["max_tokens"] * 4)


def _eighty(env) -> list[str]:
    return [env.add_txn(D, -100 - i, f"GREENBASKET STORES {i:04d}") for i in range(80)]


def test_a_bad_reply_from_a_small_model_leaves_room_for_the_repair_turn(cenv):
    """Review Focus 5: batches leave room for the client's repair turn (the conversation sent
    again with the bad reply and a note). The garbled batch is deferred; the rest are sorted."""
    env = cenv.agent
    assert cenv.local_model() == 4096
    ids = _eighty(env)
    cenv.scripted.replies = [_garbage, _garbage]
    counts = env.categorise(ids)
    assert (counts["stopped"], counts["awaiting_ai"], counts["bad_replies"]) == ("", 0, 1)
    waiting = [env.understanding.get(i).waiting for i in ids]
    assert 0 < waiting.count("deferred") == counts["deferred"] < 80
    assert counts["llm"] == 80 - counts["deferred"]
    repair = cenv.chats()[1]
    assert [m["role"] for m in repair["messages"][-2:]] == ["assistant", "user"]


def test_without_that_room_the_repair_turn_is_refused(cenv, monkeypatch):
    """The control: batches planned without room for the repair turn send every row to
    `awaiting_ai` once the first reply is bad (the client refuses the repair as too long)."""
    monkeypatch.setattr(categoriser, "REPAIR_FIXED", 0)
    monkeypatch.setattr(categoriser, "REPAIR_PER_ROW", 0)
    env = cenv.agent
    cenv.local_model()
    ids = _eighty(env)
    cenv.scripted.replies = [_garbage, _garbage]
    counts = env.categorise(ids)
    assert (counts["stopped"], counts["awaiting_ai"]) == ("awaiting_ai", 80)
    assert "too much text" in counts["ai_problem"]
