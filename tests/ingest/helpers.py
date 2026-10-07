"""Plain helper functions for the ingestion tests."""

from __future__ import annotations

from tuppence.core.accounts import Account, AccountIn
from tuppence.core.household import PersonIn
from tuppence.llm.budget import RunBudget


def use_local_model(services) -> int:
    """Make a local OpenAI-compatible model (answered by the oracle) the model for everything.
    Returns its context window."""
    conn = services.connections.create("custom", base_url="http://127.0.0.1:9000/v1")
    services.connections.test(conn.id)
    services.settings.set(
        "llm.simple_model", {"connection_id": conn.id, "model_id": "m-small"}, expected_version=0
    )
    return services.router.chain_for("read")[0][1].context_window


def add_account(services, provider: str, kind: str, nickname: str, *, last4=None) -> Account:
    people = services.household.list_people()
    owner = (
        people[0]
        if people
        else services.household.create_person(PersonIn(display_name="Alex Example", role="adult"))
    )
    return services.accounts.create(
        AccountIn(
            provider=provider,
            provider_name="Example Credit Union" if provider == "other" else None,
            kind=kind,
            nickname=nickname,
            last4=last4,
            owner_ids=[owner.id],
        )
    )


def drain(services) -> None:
    """Run every job that is ready (the analysis hand-off waits 30 s, so it stays queued)."""
    while services.worker.run_once():
        pass


def budget(calls: int = 50) -> RunBudget:
    return RunBudget(max_calls=calls, max_tokens=2_000_000, max_gbp=1.0, max_seconds=600)
