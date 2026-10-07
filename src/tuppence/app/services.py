"""Everything a request or job needs, built once at startup."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from tuppence.config.service import ConfigService
from tuppence.core.auth import LoginLimiter, Sessions, Users, prune_auth
from tuppence.core.backup import daily_backup
from tuppence.core.db import Database
from tuppence.core.household import HouseholdService
from tuppence.core.jobs import Job, JobQueue, Periodic, Worker
from tuppence.core.migrate import migrate
from tuppence.core.secrets import SecretStore, choose_secret_store
from tuppence.core.settings_store import SettingsStore
from tuppence.core.timeline import Timeline
from tuppence.net.privacy_log import PrivacyLog
from tuppence.paths import DataPaths
from tuppence.settings import RuntimeSettings

EXCLUSIVE_KINDS = frozenset({"analysis"})


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
    periodic: list[Periodic] = field(default_factory=list)
    _launch_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _launch_used: bool = field(default=False, repr=False)

    def start(self) -> None:
        self.queue.recover_running()
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
    queue = JobQueue(db)

    def backup_handler(_job: Job) -> dict[str, Any]:
        made = daily_backup(paths.db, paths.backups, date.today())
        return {"backup": str(made) if made else None}

    def prune_handler(_job: Job) -> dict[str, Any]:
        return {**prune_auth(db), "old_jobs": queue.prune_finished(days=30)}

    worker = Worker(
        queue,
        {"maintenance.daily_backup": backup_handler, "maintenance.prune_auth": prune_handler},
        exclusive_kinds=EXCLUSIVE_KINDS,
    )
    services = Services(
        runtime=runtime,
        paths=paths,
        db=db,
        settings=settings_store,
        users=Users(db),
        sessions=Sessions(db),
        limiter=LoginLimiter(db),
        household=HouseholdService(db),
        timeline=Timeline(db),
        config=ConfigService(db, settings_store, paths.config),
        queue=queue,
        worker=worker,
        privacy_log=PrivacyLog(db),
        secrets=choose_secret_store(runtime.mode, db, paths.root),
    )
    # Launch sessions from earlier launches (or another mode on this data folder) must not survive.
    services.sessions.purge_kind("launch")
    return services
