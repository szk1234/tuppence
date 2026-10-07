"""Run risky parsing (PDF, OCR, spreadsheets) in a separate process with a time
limit and, where the OS reports it, a memory limit (spec §14.2). A hostile or
broken file can hang or exhaust only the child process, never the app."""

from __future__ import annotations

import multiprocessing
import os
import pickle
import shutil
import socket
import sys
import tempfile
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from tuppence.core.errors import UserFacing

POLL_S = 0.2
MAX_RESULT_BYTES = 50 * 1024 * 1024
ADDRESS_SPACE_FACTOR = 16


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


class _NoNetworkSocket(socket.socket):
    """Stands in for socket.socket in the child: connecting, sending and creating
    internet sockets all fail. (Only local Unix sockets may still be created.)"""

    def __init__(self, family: int = -1, *args: Any, **kwargs: Any) -> None:
        if family in (socket.AF_INET, socket.AF_INET6, -1):
            _no_network()
        super().__init__(family, *args, **kwargs)

    connect = connect_ex = bind = send = sendall = sendto = sendmsg = _no_network  # type: ignore[assignment]


def _disable_network() -> None:
    """Reading a file never needs the network, so a hostile file can't use it."""
    import _socket

    socket.socket = _NoNetworkSocket  # type: ignore[misc]
    _socket.socket = _NoNetworkSocket  # type: ignore[misc,assignment]
    for module in (socket, _socket):
        for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr"):
            if hasattr(module, name):
                setattr(module, name, _no_network)
    socket.create_connection = _no_network  # type: ignore[assignment]


def _limit_memory(memory_mb: int) -> None:
    """Defence in depth next to the parent's resident-memory watch (POSIX only).

    onnxruntime reserves ~15 GB of address space for ~0.3 GB of real memory, so the
    address-space limit is far above `memory_mb`; it only stops absurd single allocations.
    Windows has no equivalent here and relies on the pixel/page/word caps and the timeout.
    """
    try:
        import resource
    except ImportError:  # Windows
        return
    try:
        if sys.platform.startswith("linux"):
            limit = memory_mb * 1024 * 1024 * ADDRESS_SPACE_FACTOR
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        elif sys.platform == "darwin":
            limit = memory_mb * 1024 * 1024 * 4
            resource.setrlimit(resource.RLIMIT_DATA, (limit, limit))
    except (ValueError, OSError):
        pass  # the watch and the caps still apply


def _child(conn: Connection, fn: Callable[..., Any], args: tuple[Any, ...], memory_mb: int) -> None:
    workdir = Path(tempfile.mkdtemp(prefix="tuppence-sandbox-"))
    try:
        os.chdir(workdir)  # anything the reader writes lands in a private, throwaway folder
        _disable_network()
        _limit_memory(memory_mb)
        payload = pickle.dumps(("ok", fn(*args)), pickle.HIGHEST_PROTOCOL)
        if len(payload) > MAX_RESULT_BYTES:
            payload = pickle.dumps(
                ("error", "This file contains more text than Tuppence can handle."),
                pickle.HIGHEST_PROTOCOL,
            )
        conn.send_bytes(payload)
    except MemoryError:
        _send_error(conn, "This file needs more memory to read than Tuppence allows.")
    except BaseException as exc:  # noqa: BLE001 - every failure goes back to the parent
        if isinstance(exc, UserFacing):  # our own message, worded for people
            _send_error(conn, str(exc))
        else:
            _send_error(conn, f"This file couldn't be read ({type(exc).__name__}).")
    finally:
        conn.close()
        os.chdir(tempfile.gettempdir())
        shutil.rmtree(workdir, ignore_errors=True)


def _send_error(conn: Connection, message: str) -> None:
    conn.send_bytes(pickle.dumps(("error", message), pickle.HIGHEST_PROTOCOL))


def run_isolated[T](fn: Callable[..., T], *args: Any, timeout_s: float, memory_mb: int = 2048) -> T:
    """Call module-level `fn(*args)` in a fresh process. The result must be picklable."""
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_child, args=(child, fn, args, memory_mb), daemon=True)
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
                    status, value = pickle.loads(  # noqa: S301 - our own child's reply
                        parent.recv_bytes(MAX_RESULT_BYTES + 4096)
                    )
                except (EOFError, OSError):
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
