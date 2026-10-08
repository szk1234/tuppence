"""Everything a request or job needs, built once at startup."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx
from langgraph.checkpoint.sqlite import SqliteSaver

from tuppence.config.service import ConfigService
from tuppence.core.accounts import AccountService
from tuppence.core.auth import LoginLimiter, Sessions, Users, prune_auth
from tuppence.core.backup import daily_backup
from tuppence.core.clock import months_ago, to_iso, utcnow
from tuppence.core.db import Database
from tuppence.core.debts import DebtService
from tuppence.core.goals import GoalService
from tuppence.core.household import HouseholdService
from tuppence.core.income import IncomeService
from tuppence.core.jobs import Handler, Job, JobQueue, Periodic, Worker
from tuppence.core.migrate import migrate
from tuppence.core.onboarding import OnboardingService
from tuppence.core.secrets import SecretStore, choose_secret_store
from tuppence.core.settings_store import SettingsStore
from tuppence.core.timeline import Timeline
from tuppence.ingest.files import StatementFiles
from tuppence.ingest.handoff import ANALYSIS_JOB, enqueue_analysis, merge_statement_ids
from tuppence.ingest.identify import fingerprint_key
from tuppence.ingest.pipeline import IngestDeps, IngestGraph
from tuppence.ingest.registry import BankPack, LayoutRegistry, LearnedLayouts, load_bank_pack
from tuppence.ingest.service import INGEST_JOB, IngestService
from tuppence.ingest.store import StatementStore
from tuppence.ingest.vision import vision_factory
from tuppence.llm.budget import BreakerBoard, UsageLedger
from tuppence.llm.catalogue import ModelCatalogue, load_baseline
from tuppence.llm.client import LLMClient
from tuppence.llm.connections import ClientFactory, ConnectionRegistry
from tuppence.llm.routing import TaskRouter
from tuppence.net import client as netclient
from tuppence.net.client import CallContext
from tuppence.net.privacy_log import PrivacyLog
from tuppence.paths import DataPaths
from tuppence.settings import RuntimeSettings

EXCLUSIVE_KINDS = frozenset({"analysis"})
# How long the privacy log and the AI usage ledger keep rows (a year, plus a month of slack
# so a full year is always there to compare against).
RETENTION_MONTHS = 13


@dataclass
class Services:
    runtime: RuntimeSettings
    paths: DataPaths
    db: Database
    settings: SettingsStore
    users: Users
    sessions: Sessions
    limiter: LoginLimiter
    household: HouseholdService
    timeline: Timeline
    config: ConfigService
    queue: JobQueue
    worker: Worker
    privacy_log: PrivacyLog
    secrets: SecretStore
    catalogue: ModelCatalogue
    client_factory: ClientFactory
    connections: ConnectionRegistry
    router: TaskRouter
    usage: UsageLedger
    breakers: BreakerBoard
    llm: LLMClient
    accounts: AccountService
    income: IncomeService
    debts: DebtService
    goals: GoalService
    onboarding: OnboardingService
    bank_pack: BankPack
    layouts: LayoutRegistry
    statements: StatementStore
    statement_files: StatementFiles
    checkpointer: SqliteSaver
    ingest: IngestService
    periodic: list[Periodic] = field(default_factory=list)
    _launch_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _launch_used: bool = field(default=False, repr=False)

    def start(self) -> None:
        self.queue.recover_running()
        self.ingest.resume_unfinished()  # statements left part-way carry on from a checkpoint
        self.worker.start()
        self.periodic = [
            Periodic(self.queue, "maintenance.daily_backup", scope_key="daily", interval_s=3600),
            Periodic(self.queue, "maintenance.prune_auth", scope_key="daily", interval_s=3600),
        ]
        for p in self.periodic:
            p.start()

    def stop(self) -> None:
        for p in self.periodic:
            p.stop()
        self.worker.stop()
        self.ingest.close()  # closes checkpoints.db, only once the worker has stopped

    def consume_launch_token(self) -> bool:
        """Mark the launch token used. Returns False if a single-use token was already spent."""
        if self.runtime.mode != "desktop":
            return True  # local mode: the printed URL stays reusable until restart
        with self._launch_lock:
            if self._launch_used:
                return False
            self._launch_used = True
            return True


def build_services(runtime: RuntimeSettings) -> Services:
    paths = DataPaths(runtime.data_dir).ensure()
    db = Database(paths.db)
    migrate(db, paths.backups)
    settings_store = SettingsStore(db)
    household = HouseholdService(db)
    queue = JobQueue(db, merges={ANALYSIS_JOB: merge_statement_ids})
    privacy_log = PrivacyLog(db)
    usage = UsageLedger(db)

    def backup_handler(_job: Job) -> dict[str, Any]:
        made = daily_backup(paths.db, paths.backups, date.today())
        return {"backup": str(made) if made else None}

    def prune_handler(_job: Job) -> dict[str, Any]:
        """Daily maintenance: expired sign-ins, old jobs, and log rows past retention."""
        cutoff = to_iso(months_ago(utcnow(), RETENTION_MONTHS))
        return {
            **prune_auth(db),
            "old_jobs": queue.prune_finished(days=30),
            "privacy_log": privacy_log.prune(cutoff),
            "llm_usage": usage.prune(cutoff),
        }

    handlers: dict[str, Handler] = {
        "maintenance.daily_backup": backup_handler,
        "maintenance.prune_auth": prune_handler,
    }
    secrets = choose_secret_store(runtime.mode, db, paths.root)
    catalogue = load_baseline()

    def client_factory(ctx: CallContext, timeout: float) -> httpx.Client:
        # Looked up at call time so tests can patch tuppence.net.client.make_client.
        return netclient.make_client(
            ctx,
            privacy_log=privacy_log,
            local_only=lambda: bool(settings_store.get("privacy.local_only")),
            timeout=timeout,
        )

    connections = ConnectionRegistry(db, secrets, catalogue, client_factory=client_factory)
    router = TaskRouter(db, settings_store, connections)
    breakers = BreakerBoard()
    llm = LLMClient(
        connections=connections,
        router=router,
        usage=usage,
        settings=settings_store,
        household=household,
        breakers=breakers,
        privacy_log=privacy_log,
    )

    accounts = AccountService(db, household)
    timeline = Timeline(db)
    income = IncomeService(db, household, accounts)
    debts = DebtService(db, household, today=date.today)
    goals = GoalService(db)
    config = ConfigService(db, settings_store, paths.config)

    bank_pack = load_bank_pack()
    layouts = LayoutRegistry(
        bank_pack, user_dir=paths.config / "importers", learned=LearnedLayouts(db)
    )
    statements = StatementStore(db)
    statement_files = StatementFiles(paths.files / "statements")
    checkpoint_conn = sqlite3.connect(paths.checkpoints_db, check_same_thread=False)
    checkpoint_conn.execute("PRAGMA journal_mode=WAL")
    checkpointer = SqliteSaver(checkpoint_conn)
    ingest = IngestService(
        store=statements,
        files=statement_files,
        queue=queue,
        checkpointer=checkpointer,
        budget_factory=lambda: llm.new_run(config.get("reader").budgets),
        graph=IngestGraph(
            IngestDeps(
                store=statements,
                files=statement_files,
                pack=bank_pack,
                registry=layouts,
                accounts=accounts,
                llm=llm,
                router=router,
                config=config,
                settings=settings_store,
                on_imported=lambda statement_id: enqueue_analysis(queue, statement_id),
                fingerprint_key=lambda: fingerprint_key(db),
                names=lambda: [p.display_name for p in household.list_people(include_retired=True)],
                prompts_dir=paths.config,
                vision_factory=vision_factory(llm, router, paths.config),
            )
        ),
    )
    handlers[INGEST_JOB] = ingest.handle_job
    # No handler for ANALYSIS_JOB yet: the worker claims only kinds it handles, so analysis jobs
    # wait in the queue for M4's analysis workflow.
    worker = Worker(queue, handlers, exclusive_kinds=EXCLUSIVE_KINDS)
    services = Services(
        runtime=runtime,
        paths=paths,
        db=db,
        settings=settings_store,
        users=Users(db),
        sessions=Sessions(db),
        limiter=LoginLimiter(db),
        household=household,
        timeline=timeline,
        config=config,
        queue=queue,
        worker=worker,
        privacy_log=privacy_log,
        secrets=secrets,
        catalogue=catalogue,
        client_factory=client_factory,
        connections=connections,
        router=router,
        usage=usage,
        breakers=breakers,
        llm=llm,
        accounts=accounts,
        income=income,
        debts=debts,
        goals=goals,
        onboarding=OnboardingService(
            db, household, timeline, accounts, income, debts, goals, settings_store, router
        ),
        bank_pack=bank_pack,
        layouts=layouts,
        statements=statements,
        statement_files=statement_files,
        checkpointer=checkpointer,
        ingest=ingest,
    )
    # Launch sessions from earlier launches (or another mode on this data folder) must not survive.
    services.sessions.purge_kind("launch")
    return services
