"""Friendly start-up failures: a plain message instead of a traceback.

Windowed desktop builds have no console (stderr is os.devnull), so there the message is also
written to `<data>/startup-error.txt`, where the user can find it.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
from pathlib import Path

from tuppence.core.clock import to_iso, utcnow
from tuppence.core.migrate import MigrationError

# What opening the data can raise: a damaged database file, a failed or unknown migration, or a
# filesystem problem (e.g. the disk filling up while the pre-migration backup is written).
STARTUP_ERRORS: tuple[type[BaseException], ...] = (sqlite3.DatabaseError, MigrationError, OSError)

ERROR_FILE = "startup-error.txt"


def startup_failure(data_dir: Path, exc: BaseException) -> str:
    if isinstance(exc, MigrationError):
        detail = str(exc)
    elif isinstance(exc, sqlite3.DatabaseError):
        detail = f"its database couldn't be opened ({exc}). The file may be damaged."
    elif isinstance(exc, OSError):
        detail = f"it couldn't read or write its data folder ({exc.strerror or exc})."
    else:
        detail = str(exc)
    backups = data_dir / "backups"
    return (
        f"Tuppence couldn't start: {detail}\n"
        f"Your data folder is {data_dir}. Backups are kept in {backups}.\n"
        "To go back to a backup: close Tuppence, move tuppence.db (and any tuppence.db-wal and "
        "tuppence.db-shm files) out of the data folder, then copy a backup there as tuppence.db."
    )


def console_hidden() -> bool:
    """True in windowed builds, where nothing printed to stderr can be seen."""
    err = sys.stderr
    return err is None or getattr(err, "name", None) == os.devnull


def report(message: str, data_dir: Path | None) -> None:
    """Print a start-up problem; with no console, also leave it in the data folder."""
    print(message, file=sys.stderr)
    if data_dir is not None and console_hidden():
        with contextlib.suppress(OSError):  # nowhere left to report it
            (data_dir / ERROR_FILE).write_text(f"{to_iso(utcnow())}\n{message}\n", encoding="utf-8")


def clear_report(data_dir: Path) -> None:
    """Remove an old start-up error once Tuppence has started cleanly."""
    with contextlib.suppress(OSError):
        (data_dir / ERROR_FILE).unlink(missing_ok=True)
