"""Where Tuppence keeps its data. Never the working directory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "Tuppence"


class DataDirError(RuntimeError):
    """The data folder can't be created or written."""


def resolve_data_dir(override: str | os.PathLike[str] | None = None) -> Path:
    raw = (
        override or os.environ.get("TUPPENCE_DATA_DIR") or user_data_dir(APP_NAME, appauthor=False)
    )
    path = Path(raw).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        reason = exc.strerror or str(exc)
        raise DataDirError(
            f"Tuppence can't write to its data folder {path}: {reason}. "
            "Set TUPPENCE_DATA_DIR to a writable folder."
        ) from exc
    return path


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "tuppence.db"

    @property
    def checkpoints_db(self) -> Path:
        return self.root / "checkpoints.db"

    @property
    def files(self) -> Path:
        return self.root / "files"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def config(self) -> Path:
        return self.root / "config"

    def ensure(self) -> DataPaths:
        for directory in (self.root, self.files, self.backups, self.config):
            directory.mkdir(parents=True, exist_ok=True)
        return self
