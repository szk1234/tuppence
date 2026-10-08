"""The analysis workflow on the app's own services: statement import, the hand-off, the job,
restarts, the real LLM client (every request answered by a scripted server, then the
oracle; no network) and a database from before M4."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from agents.helpers import oracle_handler
from fakes.scripted import Scripted
from ingest.helpers import add_account, cloud_model, drain, threads, use_local_model
from tuppence.app.services import build_services
from tuppence.core import migrate as migrate_module
from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.core.secrets import SecretUnreadable
from tuppence.ingest.handoff import ANALYSIS_JOB, ANALYSIS_SCOPE
from tuppence.settings import RuntimeSettings

STARLING_HEAD = (
    "Date,Counter Party,Reference,Type,Amount (GBP),Balance (GBP),Spending Category,Notes\n"
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    """(services, scripted): the real app core; every AI request goes to `scripted`."""
    scripted = Scripted()
    scripted.handler = oracle_handler(scripted)
    from tuppence.net import client as netclient

    real = netclient.make_client

    def fake_make_client(ctx, *, privacy_log, local_only, timeout, transport=None):
        return real(
            ctx,
            privacy_log=privacy_log,
            local_only=local_only,
            timeout=timeout,
            transport=scripted.transport(),
        )

    monkeypatch.setattr(netclient, "make_client", fake_make_client)
    services = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    services.llm.sleep = lambda s: None
    yield services, scripted
    services.checkpointer.conn.close()


def starling_csv(rows) -> bytes:
    """A Starling export: (date, counter party, reference, type, amount)."""
    balance = 1000_00
    lines = [STARLING_HEAD]
    for day, party, reference, kind, pence in rows:
        balance += pence
        lines.append(
            f"{day},{party},{reference},{kind},{pence / 100:.2f},{balance / 100:.2f},GENERAL,\n"
        )
    return "".join(lines).encode()


ORDINARY = [
    ("01/10/2026", "Greenbasket Stores", "REF 0001", "CARD", -4218),
    ("05/10/2026", "Little Cafe", "REF 0005", "CARD", -340),
    ("20/10/2026", "Northline Rail", "REF 0020", "CARD", -2890),
]


def import_csv(services, data: bytes, name: str = "starling.csv"):
    outcome = services.ingest.upload(name, data)
    drain(services)
    record = services.statements.get(outcome.record.id)
    assert record.status == "imported", (record.error, record.check_errors)
    return record


def analyse(services):
    """Run the pending analysis now (past its 30 s debounce)."""
    services.queue.expedite(ANALYSIS_JOB, scope_key=ANALYSIS_SCOPE)
    drain(services)
    return services.analysis.runs()[0]


def analysis_jobs(services):
    return [j for j in services.queue.list() if j.kind == ANALYSIS_JOB]


def understanding_of(services, account_id):
    """{description: understanding} for every row of the account."""
    with services.db.connection() as conn:
        rows = conn.execute(
            'SELECT t.raw_description, t.id FROM "transaction" t WHERE t.account_id = ?',
            [account_id],
        ).fetchall()
    return {r[0]: services.understanding.get(r[1]) for r in rows}


# --- the hand-off runs ------------------------------------------------------------------------


def test_an_imported_statement_is_understood(app):
    services, scripted = app
    use_local_model(services)
    account = add_account(services, "starling", "current", "Starling")
    record = import_csv(services, starling_csv(ORDINARY))
    run = analyse(services)
    assert run.status == "done" and run.statement_ids == [record.id], run.summary
    assert run.llm_calls >= 1 and "by the AI" in run.summary
    assert services.statements.get(record.id).analysis_state == "done"
    assert all(u.status != "unknown" for u in understanding_of(services, account.id).values())
    assert [j.status for j in analysis_jobs(services)] == ["done"]
    assert not any(t.startswith("analysis:") for t in threads(services))


# --- G5: an AI problem ends the run partial, never failed --------------------------------------


def _unreadable(connection_id):
    raise SecretUnreadable("The saved API key can't be read. Enter it again in Settings › AI.")


@pytest.mark.parametrize(
    "problem, text",
    [
        ("notice", "Confirm what OpenAI will see before Tuppence uses it"),
        ("local only", "Local only is on"),
        ("unreadable key", "The saved API key can't be read"),
        ("no model", "Choose an AI model in Settings › AI."),
    ],
)
def test_an_ai_problem_ends_the_run_partial_with_the_reason(app, monkeypatch, problem, text):
    services, scripted = app
    if problem != "no model":
        cloud_model(services, acknowledge=problem != "notice")
    if problem == "local only":
        services.settings.set("privacy.local_only", True, expected_version=0)
    if problem == "unreadable key":
        monkeypatch.setattr(services.connections, "api_key", _unreadable)
    account = add_account(services, "starling", "current", "Starling")
    record = import_csv(services, starling_csv(ORDINARY))
    run = analyse(services)
    assert [j.status for j in analysis_jobs(services)] == ["done"]  # the job never fails
    assert run.status == "partial" and run.stopped_reason == "awaiting_ai"
    assert "3 transactions are waiting for an AI model" in run.summary and text in run.summary
    rows = understanding_of(services, account.id).values()
    assert [u.waiting for u in rows] == ["awaiting_ai"] * 3
    assert services.statements.get(record.id).analysis_state == "done"
    assert services.analysis.waiting() == {"awaiting_ai": 3}
    assert scripted.requests == []  # nothing left the machine


# --- G6: what reaches a model -------------------------------------------------------------------

SECRETS = ["20-00-00", "200000", "12345678", "40-11-62", "401162", "31926819",
           "4929123412341234", "4929 1234 1234 1234"]  # fmt: skip


def test_account_and_card_details_in_a_statement_never_reach_a_model(app):
    """A CSV holding a sort code and account number, a transfer naming another account and a
    full card number is imported and analysed with a local model: none of those digits is in
    any request (spec §4.6)."""
    services, scripted = app
    use_local_model(services)
    account = add_account(services, "starling", "current", "Starling")
    rows = [
        ("01/10/2026", "SORT 20-00-00 ACC 12345678", "RENT", "FASTER PAYMENT", -50000),
        ("02/10/2026", "TFR TO SAVINGS 40-11-62 31926819", "MONTHLY", "TRANSFER", -20000),
        ("03/10/2026", "Greenbasket Stores", "CARD 4929123412341234", "CARD", -4218),
        ("04/10/2026", "Little Cafe", "CARD 4929 1234 1234 1234", "CARD", -340),
    ]
    import_csv(services, starling_csv(rows))
    run = analyse(services)
    assert run.status == "done", run.summary
    stored = understanding_of(services, account.id)
    assert any("12345678" in description for description in stored)  # kept on this machine
    understanding = [r for r in scripted.requests if "TRANSACTIONS:" in json.dumps(r)]
    assert understanding  # the rows did go to the model
    sent = json.dumps(scripted.requests)
    squashed = sent.replace(" ", "").replace("-", "")
    leaked = [s for s in SECRETS if s in sent or s.replace(" ", "").replace("-", "") in squashed]
    assert leaked == []
    assert "Greenbasket" in sent and "SAVINGS" in sent  # merchant words still go


# --- R-M4-2: the person's corrections survive a re-read -----------------------------------------


def test_a_correction_survives_try_again_and_remove_then_upload_again(app):
    services, _ = app
    use_local_model(services)
    account = add_account(services, "starling", "current", "Starling")
    data = starling_csv(ORDINARY)
    record = import_csv(services, data)
    analyse(services)
    cafe = understanding_of(services, account.id)["Little Cafe REF 0005"]
    assert cafe.category_id != "gifts"
    services.understanding.set_by_person(
        cafe.transaction_id, expected_version=cafe.version, category_id="gifts"
    )
    record = services.statements.get(record.id)
    services.ingest.retry(record.id, expected_version=record.version)  # Try again
    drain(services)
    assert services.statements.get(record.id).status == "imported"
    run = analyse(services)
    assert run.status == "done" and record.id in run.statement_ids
    kept = understanding_of(services, account.id)["Little Cafe REF 0005"]
    assert (kept.status, kept.category_id, kept.decided_by) == ("confirmed", "gifts", "human")
    services.ingest.delete(record.id)  # Remove, then upload the same file again
    assert understanding_of(services, account.id) == {}
    again = import_csv(services, data, "again.csv")
    run = analyse(services)
    assert run.status == "done" and again.id in run.statement_ids
    back = understanding_of(services, account.id)["Little Cafe REF 0005"]
    assert back.transaction_id != cafe.transaction_id  # a new row, the same transaction
    assert (back.status, back.category_id) == ("confirmed", "gifts")
    assert back.evidence.get("carried") == "reread"


# --- D8: a restart carries on where the run stopped ---------------------------------------------


def test_a_run_interrupted_by_a_restart_carries_on_without_asking_the_ai_again(
    app, tmp_path, monkeypatch
):
    services, scripted = app
    use_local_model(services)
    account = add_account(services, "starling", "current", "Starling")
    record = import_csv(services, starling_csv(ORDINARY))
    services.queue.expedite(ANALYSIS_JOB, scope_key=ANALYSIS_SCOPE)
    job = services.queue.claim(kinds=frozenset({ANALYSIS_JOB}))
    assert job is not None

    class PowerCut(BaseException):
        pass

    def die(*args, **kwargs):
        raise PowerCut()  # the process dies once the Categoriser's work is checkpointed

    monkeypatch.setattr(services.analysis.deps.transfers, "run", die)
    with pytest.raises(PowerCut):
        services.analysis.handle_job(job)
    asked = len(scripted.requests)
    assert asked >= 1 and f"analysis:{job.id}" in threads(services)
    services.checkpointer.conn.close()

    later = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    later.llm.sleep = lambda s: None
    monkeypatch.setattr(later.worker, "start", lambda: None)  # jobs run below, one at a time
    try:
        later.start()  # recovers the job; the sweeps keep its checkpoints
        assert f"analysis:{job.id}" in threads(later)
        assert later.queue.get(job.id).status == "queued"
        resumed = later.queue.claim(kinds=frozenset({ANALYSIS_JOB}))
        assert resumed is not None and resumed.id == job.id
        out = later.analysis.handle_job(resumed)
        later.queue.complete(resumed.id, out)
    finally:
        later.stop()
    assert len(scripted.requests) == asked  # carried on: nothing asked twice
    run = later.analysis.runs()[0]
    assert run.id == f"ar_{job.id}" and run.status == "done"
    # The Categoriser's work from before the restart is this run's (a run started afresh
    # would find every row already filed and report none of it).
    assert run.counts["categoriser"]["llm"] == 3 and "3 by the AI" in run.summary
    assert later.statements.get(record.id).analysis_state == "done"
    assert all(u.status != "unknown" for u in understanding_of(later, account.id).values())
    assert f"analysis:{job.id}" not in threads_of(tmp_path)


def threads_of(data_dir):
    import sqlite3

    from tuppence.paths import DataPaths

    with sqlite3.connect(DataPaths(data_dir).checkpoints_db) as conn:
        return {r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM checkpoints")}


# --- a database from before M4 -------------------------------------------------------------------


def test_knowledge_arrives_over_an_m3_database_and_its_waiting_analysis_runs(tmp_path, monkeypatch):
    """Migration 0010 over M3 data: every transaction gets an understanding row, nothing is
    lost, and the analysis job M3 left queued (no handler then) runs."""
    from tuppence.paths import DataPaths

    paths = DataPaths(tmp_path).ensure()
    db = Database(paths.db)
    with monkeypatch.context() as m:
        before = [v for v in migrate_module.available_migrations() if v[0] < "0010"]
        m.setattr(migrate_module, "available_migrations", lambda: before)
        migrate(db, paths.backups)
    past = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.transaction() as conn:
        assert (
            conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'understanding'").fetchone()
            is None
        )  # an M3 database
        conn.execute(
            "INSERT INTO person (id, display_name, role, created_at, updated_at)"
            " VALUES ('p_1', 'Alex Example', 'adult', 'x', 'x')"
        )
        conn.execute(
            "INSERT INTO account (id, provider, provider_name, kind, nickname, created_at,"
            " updated_at) VALUES ('a_1', 'starling', 'Starling', 'current', 'Main', 'x', 'x')"
        )
        conn.execute("INSERT INTO account_owner (account_id, person_id) VALUES ('a_1', 'p_1')")
        for n, statement in enumerate(("s_1", "s_2")):
            conn.execute(
                "INSERT INTO statement (id, account_id, file_sha256, file_ext, original_filename,"
                " format, status, period_start, period_end, analysis_state, created_at,"
                " updated_at) VALUES (?, 'a_1', ?, 'csv', 'x.csv', 'csv', 'imported',"
                " '2026-10-01', '2026-10-31', 'pending', ?, ?)",
                [statement, f"{n:064d}", past, past],
            )
        rows = [
            ("t_1", "s_1", "2026-10-01", -4218, "GREENBASKET STORES"),
            ("t_2", "s_1", "2026-10-05", -340, "LITTLE CAFE"),
            ("t_3", "s_2", "2026-10-20", -2890, "NORTHLINE RAIL"),
        ]
        for tid, statement, day, pence, text in rows:
            conn.execute(
                'INSERT INTO "transaction" (id, account_id, statement_id, date, amount_pence,'
                " raw_description, source_ref, fingerprint, occurrence, created_at)"
                " VALUES (?, 'a_1', ?, ?, ?, ?, 'L1', ?, 0, ?)",
                [tid, statement, day, pence, text, f"fp_{tid}", past],
            )
            conn.execute(
                "INSERT INTO statement_transaction (statement_id, transaction_id, source_ref,"
                " match) VALUES (?, ?, 'L1', 'new')",
                [statement, tid],
            )
        conn.execute(  # s_2 also covers t_1 (found again: an exact duplicate)
            "INSERT INTO statement_transaction (statement_id, transaction_id, source_ref, match)"
            " VALUES ('s_2', 't_1', 'L7', 'exact')"
        )
        conn.execute(
            "INSERT INTO job (kind, scope_key, payload, status, run_after, created_at)"
            " VALUES ('analysis', 'household', ?, 'queued', ?, ?)",
            [json.dumps({"statement_ids": ["s_1", "s_2"]}), past, past],
        )
        snapshot = [tuple(r) for r in conn.execute('SELECT * FROM "transaction" ORDER BY id')]
        links = [
            tuple(r) for r in conn.execute("SELECT * FROM statement_transaction ORDER BY 1, 2")
        ]

    services = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    try:
        with services.db.connection() as conn:
            assert [tuple(r) for r in conn.execute('SELECT * FROM "transaction" ORDER BY id')] == (
                snapshot
            )
            assert [
                tuple(r) for r in conn.execute("SELECT * FROM statement_transaction ORDER BY 1, 2")
            ] == links
            understood = [
                tuple(r)
                for r in conn.execute(
                    "SELECT transaction_id, status, waiting FROM understanding ORDER BY 1"
                )
            ]
        assert understood == [
            ("t_1", "unknown", None),
            ("t_2", "unknown", None),
            ("t_3", "unknown", None),
        ]
        assert any(paths.backups.glob("tuppence-pre-0010_knowledge-*.db"))
        assert services.worker.run_once()  # M3's queued job: M4 has its handler
        [job] = analysis_jobs(services)
        assert job.status == "done"
        run = services.analysis.runs()[0]
        assert run.status == "partial" and run.counts["categoriser"]["scope"] == 3
        assert {services.statements.get(s).analysis_state for s in ("s_1", "s_2")} == {"done"}
        assert services.analysis.waiting() == {"awaiting_ai": 3}  # no model yet: they wait
    finally:
        services.checkpointer.conn.close()
