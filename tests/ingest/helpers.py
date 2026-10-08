"""Plain helper functions for the ingestion tests."""

from __future__ import annotations

from pathlib import Path

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


def upload(services, fixtures: Path, relative: str, data: bytes | None = None):
    path = fixtures / relative
    return services.ingest.upload(path.name, data if data is not None else path.read_bytes())


def threads(services) -> set[str]:
    """Thread ids that still have checkpoints in checkpoints.db."""
    return {c.config["configurable"]["thread_id"] for c in services.checkpointer.list(None)}


def cloud_model(services, *, acknowledge: bool) -> str:
    """A cloud connection (answered by the scripted fake) as the model for everything."""
    conn = services.connections.create(
        "openai", api_key="sk-x", base_url="http://127.0.0.1:9100/v1"
    )
    services.connections.test(conn.id)
    if acknowledge:
        services.connections.acknowledge_notice(
            conn.id, expected_version=services.connections.get(conn.id).version
        )
    services.settings.set(
        "llm.simple_model", {"connection_id": conn.id, "model_id": "m-small"}, expected_version=0
    )
    return conn.id


def stored_rows(services, account_id: str) -> int:
    with services.db.connection() as conn:
        return conn.execute(
            'SELECT count(*) FROM "transaction" WHERE account_id = ?', [account_id]
        ).fetchone()[0]


def budget(calls: int = 50) -> RunBudget:
    return RunBudget(max_calls=calls, max_tokens=2_000_000, max_gbp=1.0, max_seconds=600)


def parse_pages(services, pages, account_kind="current", *, limits=None, registry=None):
    """Run the AI read and parse steps over statement pages given as lists of text lines.
    Returns (outcome, document)."""
    import datetime as dt

    from tuppence.ingest.identify import identify
    from tuppence.ingest.parse import ReaderLimits, parse_document
    from tuppence.ingest.registry import LayoutRegistry, load_bank_pack
    from tuppence.ingest.textprep import pages_document

    window = use_local_model(services)
    pack = load_bank_pack()
    registry = registry or LayoutRegistry(pack)
    doc = pages_document(pages, sha256="x", kind="pdf")
    evidence = identify(doc, pack=pack, registry=registry, key=b"test-key")
    out = parse_document(
        doc,
        Path("unused.pdf"),
        evidence,
        account_kind,
        registry=registry,
        llm=services.llm,
        run=budget(),
        context_window=window,
        today=dt.date(2026, 11, 1),
        limits=limits or ReaderLimits(),
    )
    return out, doc
