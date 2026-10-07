"""Where Tuppence keeps its data. Never the working directory."""

from __future__ import annotations

import errno
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO

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


class InstanceLocked(RuntimeError):
    """Another Tuppence process already holds this data folder."""


class InstanceLockError(RuntimeError):
    """The data folder can't be locked for a reason other than another instance holding it."""


def _is_contention(exc: OSError) -> bool:
    if isinstance(exc, BlockingIOError):
        return True
    codes = {errno.EACCES} if sys.platform == "win32" else {errno.EWOULDBLOCK, errno.EAGAIN}
    return exc.errno in codes


class InstanceLock:
    """An exclusive OS lock on `<data>/tuppence.lock`, held until released or the process exits."""

    def __init__(self, handle: IO[bytes]) -> None:
        self._handle: IO[bytes] | None = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if self in _held_locks:
            _held_locks.remove(self)
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


_held_locks: list[InstanceLock] = []  # keeps every lock alive for the process lifetime


def acquire_instance_lock(data_dir: str | os.PathLike[str]) -> InstanceLock:
    path = Path(data_dir) / "tuppence.lock"
    handle: IO[bytes] | None = None
    try:
        handle = open(path, "a+b")  # noqa: SIM115 - held on purpose
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if handle is not None:
            handle.close()
        if handle is not None and _is_contention(exc):
            raise InstanceLocked("Tuppence is already running with this data folder.") from exc
        reason = exc.strerror or str(exc)
        raise InstanceLockError(
            f"Tuppence couldn't lock its data folder {Path(data_dir)}: {reason}"
        ) from exc
    lock = InstanceLock(handle)
    _held_locks.append(lock)
    return lock
