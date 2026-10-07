"""Everything a request or job needs, built once at startup."""

from __future__ import annotations

from dataclasses import dataclass

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.core.settings_store import SettingsStore
from tuppence.paths import DataPaths
from tuppence.settings import RuntimeSettings


@dataclass
class Services:
    runtime: RuntimeSettings
    paths: DataPaths
    db: Database
    settings: SettingsStore


def build_services(runtime: RuntimeSettings) -> Services:
    paths = DataPaths(runtime.data_dir).ensure()
    db = Database(paths.db)
    migrate(db, paths.backups)
    return Services(runtime=runtime, paths=paths, db=db, settings=SettingsStore(db))
