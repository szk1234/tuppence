"""The Commitments specialist through the real LLM client: its budgets, usage ledger and the
problems that end a run partial, never failed (G5). No network."""

from datetime import date

import pytest

from agents.helpers import file_as, manifest
from tuppence.agents.commitments import Commitments, CommitmentsDeps
from tuppence.core.secrets import SecretUnreadable
from tuppence.knowledge.commitments import CommitmentStore


def months(day, n, start=(2026, 1)):
    y, m = start
    out = []
    for _ in range(n):
        out.append(date(y, m, day))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def seed(env):
    for d in months(14, 4):
        t = env.add_txn(d, -999, "STREAMLY")
        file_as(env, t, "subscriptions.tv-streaming", env.merchants)
    for d in months(5, 4):
        t = env.add_txn(d, -1500, "MYSTERY CLUB")
        file_as(env, t, "other", env.merchants)


def specialist(env):
    store = CommitmentStore(env.db)
    return Commitments(
        CommitmentsDeps(
            db=env.db,
            merchants=env.merchants,
            store=store,
            llm=env.llm,
            context_window=env.window_for,
            manifest=lambda: manifest("commitments"),
        )
    ), store


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
def test_an_ai_problem_ends_partial_with_the_reason_and_code_still_detects(
    cenv, monkeypatch, problem, text
):
    env, services = cenv.agent, cenv.services
    cenv.cloud_model(acknowledge=problem != "notice")
    if problem == "local only":
        services.settings.set("privacy.local_only", True, expected_version=0)
    if problem == "unreadable key":
        monkeypatch.setattr(services.connections, "api_key", _unreadable)
    seed(env)
    commitments, store = specialist(env)
    counts = commitments.run(run_id="r1", budget=None)
    assert counts["awaiting_ai"] == 1 and text in counts["ai_problem"]
    assert [c.name for c in store.list()] == ["Streamly"]  # detected by code
    assert cenv.chats() == []  # nothing left the machine


def test_usage_rows_group_by_the_analysis_run(cenv):
    env = cenv.agent
    cenv.local_model()
    seed(env)
    commitments, store = specialist(env)
    budget = env.context().budget("commitments")
    counts = commitments.run(run_id="run_77", budget=budget)
    assert counts["labelled"] == 1 and budget.calls == 1
    with cenv.services.db.connection() as conn:
        usage = [tuple(r) for r in conn.execute("SELECT task, run_id, ok FROM llm_usage")]
    assert usage == [("categorise", "run_77", 1)]
    assert [c.name for c in store.list()] == ["Streamly"]  # the oracle labels "other" none
