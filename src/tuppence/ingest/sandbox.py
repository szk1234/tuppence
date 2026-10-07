"""Run risky parsing (PDF, OCR, spreadsheets) in a separate process with a time
limit and, where the OS reports it, a memory limit (spec §14.2). A hostile or
broken file can hang or exhaust only the child process, never the app."""

from __future__ import annotations

import multiprocessing
import socket
import sys
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from tuppence.core.errors import UserFacing

POLL_S = 0.2


class SandboxError(UserFacing, RuntimeError):
    """Its message is safe to show to the person."""


class SandboxTimeout(SandboxError):
    pass


class SandboxFailed(SandboxError):
    pass


def resident_mb(pid: int) -> float | None:
    """Resident memory of a process in MiB (Linux), else None."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        return None
    return None


def _no_network(*_args: Any, **_kwargs: Any) -> Any:
    raise OSError("Network access is switched off while reading files.")


def _disable_network() -> None:
    """Reading a file never needs the network, so a hostile file can't use it."""
    socket.socket.connect = _no_network  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = _no_network  # type: ignore[method-assign,assignment]
    socket.create_connection = _no_network  # type: ignore[assignment]


def _child(conn: Connection, fn: Callable[..., Any], args: tuple[Any, ...]) -> None:
    try:
        _disable_network()
        conn.send(("ok", fn(*args)))
    except MemoryError:
        conn.send(("error", "This file needs more memory to read than Tuppence allows."))
    except BaseException as exc:  # noqa: BLE001 - every failure goes back to the parent
        conn.send(("error", f"This file couldn't be read ({type(exc).__name__})."))
    finally:
        conn.close()


def run_isolated[T](fn: Callable[..., T], *args: Any, timeout_s: float, memory_mb: int = 2048) -> T:
    """Call module-level `fn(*args)` in a fresh process. The result must be picklable."""
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_child, args=(child, fn, args), daemon=True)
    process.start()
    child.close()
    deadline = time.monotonic() + timeout_s
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SandboxTimeout(
                    f"Reading this file took longer than {int(timeout_s)} seconds, "
                    "so it was stopped."
                )
            if parent.poll(min(POLL_S, remaining)):
                try:
                    status, value = parent.recv()
                except EOFError:
                    raise SandboxFailed(
                        "The file reader stopped unexpectedly. The file may be damaged."
                    ) from None
                break
            used = resident_mb(process.pid or 0)
            if used is not None and used > memory_mb:
                raise SandboxFailed("This file needs more memory to read than Tuppence allows.")
            if not process.is_alive() and not parent.poll(0):
                raise SandboxFailed(
                    "The file reader stopped unexpectedly. The file may be damaged."
                )
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        parent.close()
    if status == "ok":
        return value
    raise SandboxFailed(value)
