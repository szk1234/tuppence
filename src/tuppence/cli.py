"""Command line: tuppence serve | desktop | version."""

from __future__ import annotations

import argparse
import multiprocessing
import os
import secrets
import socket
import sys
import webbrowser
from pathlib import Path
from typing import NoReturn

from tuppence import __version__
from tuppence.paths import (
    DataDirError,
    InstanceLocked,
    InstanceLockError,
    acquire_instance_lock,
    resolve_data_dir,
)
from tuppence.settings import RuntimeSettings


def host_usable(host: str) -> bool:
    """Whether Tuppence could listen on `host`: an address, or a name that resolves here."""
    if not host or len(host) > 253 or host.startswith("-"):
        return False
    try:
        return bool(socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
    except (OSError, UnicodeError, ValueError):  # gaierror, or a name that can't be encoded
        return False


def port_available(host: str, port: int) -> bool:
    try:
        family, _type, _proto, _canon, sockaddr = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )[0]
    except (socket.gaierror, IndexError, UnicodeError, ValueError):
        return False
    with socket.socket(family, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(sockaddr)
        except OSError:
            return False
    return True


def env_flag(name: str) -> bool:
    """True when an environment variable is set to 1/true/yes/on (any case)."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _serve(args: argparse.Namespace) -> int:
    try:
        data_dir = resolve_data_dir(args.data_dir)
    except DataDirError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        lock = acquire_instance_lock(data_dir)
    except (InstanceLocked, InstanceLockError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        return _serve_locked(args, data_dir)
    finally:
        lock.release()


def _serve_locked(args: argparse.Namespace, data_dir: Path) -> int:
    import uvicorn

    from tuppence.app import create_app
    from tuppence.startup import STARTUP_ERRORS, startup_failure

    launch_token = secrets.token_urlsafe(32) if args.mode == "local" else None
    settings = RuntimeSettings.for_mode(
        args.mode,
        data_dir=data_dir,
        host=args.host,
        port=args.port,
        launch_token=launch_token,
        secure_cookies=args.secure_cookies or env_flag("TUPPENCE_SECURE_COOKIES"),
    )
    if not host_usable(settings.host):
        print(
            f"--host {settings.host[:80]!r} isn't a host name or address Tuppence can use. "
            "Try 127.0.0.1 (this computer only) or 0.0.0.0 (your network).",
            file=sys.stderr,
        )
        return 2
    if not port_available(settings.host, settings.port):
        print(
            f"Port {settings.port} is already in use. "
            f"Try another with --port, e.g. --port {settings.port + 1}.",
            file=sys.stderr,
        )
        return 2
    try:
        app = create_app(settings)  # opens and migrates the database
    except STARTUP_ERRORS as exc:
        print(startup_failure(data_dir, exc), file=sys.stderr)
        return 2
    shown_host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host  # noqa: S104
    url = f"http://{shown_host}:{settings.port}/"
    open_url = f"{url}auth/launch?token={launch_token}" if launch_token else url
    print(f"Tuppence {__version__} running at {open_url}  (data: {data_dir})")
    if settings.mode == "local" and not args.no_browser:
        webbrowser.open(open_url)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
    return 0


def _desktop(args: argparse.Namespace) -> int:
    from tuppence.desktop.launcher import run_desktop

    return run_desktop(data_dir=args.data_dir, smoke=args.smoke, smoke_out=args.smoke_out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tuppence", description="Tuppence: a private AI money coach."
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--mode", choices=["local", "server"], default="local")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--data-dir", default=None)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument(
        "--secure-cookies",
        action="store_true",
        help="mark the sign-in cookie Secure; only when served over HTTPS"
        " (or set TUPPENCE_SECURE_COOKIES=1)",
    )
    serve.set_defaults(func=_serve)

    desktop = sub.add_parser("desktop", help="run the desktop app window")
    desktop.add_argument("--data-dir", default=None)
    desktop.add_argument(
        "--smoke", action="store_true", help="start, health-check, exit (no window)"
    )
    desktop.add_argument("--smoke-out", default=None, help="write smoke result JSON to this file")
    desktop.set_defaults(func=_desktop)

    version = sub.add_parser("version", help="print the version")
    version.set_defaults(func=lambda _a: print(__version__) or 0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve", *(argv or [])])
    return int(args.func(args))


def run() -> NoReturn:
    multiprocessing.freeze_support()
    sys.exit(main())
