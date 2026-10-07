"""Everything a request or job needs, built once at startup."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from tuppence.core.auth import LoginLimiter, Sessions, Users
from tuppence.core.db import Database
from tuppence.core.household import HouseholdService
from tuppence.core.migrate import migrate
from tuppence.core.settings_store import SettingsStore
from tuppence.core.timeline import Timeline
from tuppence.paths import DataPaths
from tuppence.settings import RuntimeSettings


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
    _launch_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _launch_used: bool = field(default=False, repr=False)

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
    services = Services(
        runtime=runtime,
        paths=paths,
        db=db,
        settings=SettingsStore(db),
        users=Users(db),
        sessions=Sessions(db),
        limiter=LoginLimiter(db),
        household=HouseholdService(db),
        timeline=Timeline(db),
    )
    # Launch sessions from earlier launches (or another mode on this data folder) must not survive.
    services.sessions.purge_kind("launch")
    return services
