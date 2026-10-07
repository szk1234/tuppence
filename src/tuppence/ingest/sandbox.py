"""Run risky parsing (PDF, OCR, spreadsheets) in a separate process with a time
limit and, where the OS reports it, a memory limit (spec §14.2). A hostile or
broken file can hang or exhaust only the child process, never the app."""

from __future__ import annotations

import base64
import json
import multiprocessing
import os
import re
import shutil
import socket
import sys
import tempfile
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, overload

from tuppence.core.errors import UserFacing
from tuppence.ingest.refusals import MESSAGES, OutOfMemory, ReplyTooLarge
from tuppence.ingest.results import BadReply, parse_scalar

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


def _json_default(value: object) -> object:
    if isinstance(value, bytes):
        return {"$b64": base64.b64encode(value).decode("ascii")}
    raise TypeError(type(value).__name__)


def _child(conn: Connection, fn: Callable[..., Any], args: tuple[Any, ...], memory_mb: int) -> None:
    """Runs `fn` and sends the parent either `{"ok": result}` or `{"error": "<TypeName>"}`,
    as JSON bytes. The parent never unpickles anything from here."""
    workdir = Path(tempfile.mkdtemp(prefix="tuppence-sandbox-"))
    try:
        os.chdir(workdir)  # anything the reader writes lands in a private, throwaway folder
        _disable_network()
        _limit_memory(memory_mb)
        payload = json.dumps({"ok": fn(*args)}, default=_json_default, allow_nan=False).encode()
        if len(payload) > MAX_RESULT_BYTES:
            raise ReplyTooLarge
        conn.send_bytes(payload)
    except MemoryError:
        _send_error(conn, OutOfMemory.__name__)
    except BaseException as exc:  # noqa: BLE001 - every failure goes back to the parent
        _send_error(conn, type(exc).__name__)
    finally:
        conn.close()
        os.chdir(tempfile.gettempdir())
        shutil.rmtree(workdir, ignore_errors=True)


def _send_error(conn: Connection, name: str) -> None:
    conn.send_bytes(json.dumps({"error": name}).encode())


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,60}")


def _error_message(name: object) -> str:
    """Plain words for an error name from the child. Only names Tuppence registered have
    their own message; any other name is shown only if it looks like a class name."""
    if isinstance(name, str) and name in MESSAGES:
        return MESSAGES[name]
    if isinstance(name, str) and _NAME.fullmatch(name):
        return f"This file couldn't be read ({name})."
    return "This file couldn't be read."


def _decode_reply[T](raw: bytes, parse: Callable[[Any], T]) -> T:
    """The child's reply: JSON, checked by `parse`. Anything else is a failed read."""
    try:
        reply = json.loads(raw, parse_constant=_reject_constant)
        if isinstance(reply, dict) and set(reply) == {"error"}:
            raise SandboxFailed(_error_message(reply["error"]))
        if not isinstance(reply, dict) or set(reply) != {"ok"}:
            raise BadReply("unexpected reply")
        return parse(reply["ok"])
    except SandboxFailed:
        raise
    except (ValueError, RecursionError, UnicodeDecodeError):  # BadReply is a ValueError
        raise SandboxFailed("The file reader sent back something unexpected.") from None


def _reject_constant(name: str) -> object:
    raise ValueError(name)  # NaN and Infinity aren't JSON


@overload
def run_isolated(
    fn: Callable[..., Any], *args: Any, timeout_s: float, memory_mb: int = 2048
) -> Any: ...


@overload
def run_isolated[T](
    fn: Callable[..., Any],
    *args: Any,
    timeout_s: float,
    memory_mb: int = 2048,
    parse: Callable[[Any], T],
) -> T: ...


def run_isolated(
    fn: Callable[..., Any],
    *args: Any,
    timeout_s: float,
    memory_mb: int = 2048,
    parse: Callable[[Any], Any] = parse_scalar,
) -> Any:
    """Call module-level `fn(*args)` in a fresh process and return `parse(its result)`.

    `args` reach the child through the spawn arguments (the parent is trusted). The
    child's reply comes back as JSON only: it is size-capped, parsed with `json.loads`
    and checked by `parse` (a validator from `tuppence.ingest.results`). Nothing the child
    sends is ever unpickled, so a compromised parser can't run code in the app.
    """
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
                    raw = parent.recv_bytes(MAX_RESULT_BYTES + 4096)
                except EOFError:
                    raise SandboxFailed(
                        "The file reader stopped unexpectedly. The file may be damaged."
                    ) from None
                except OSError:  # a reply longer than the cap
                    raise SandboxFailed("The file reader sent back something unexpected.") from None
                break
            used = resident_mb(process.pid or 0)
            if used is not None and used > memory_mb:
                raise SandboxFailed(OutOfMemory.message)
            if not process.is_alive() and not parent.poll(0):
                raise SandboxFailed(
                    "The file reader stopped unexpectedly. The file may be damaged."
                )
    finally:
        if process.is_alive():
            process.kill()
        process.join(5)
        parent.close()
    return _decode_reply(raw, parse)
