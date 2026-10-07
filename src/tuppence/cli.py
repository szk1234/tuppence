"""Command line: tuppence serve | desktop | version."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import webbrowser
from typing import NoReturn

from tuppence import __version__
from tuppence.paths import DataDirError, resolve_data_dir
from tuppence.settings import RuntimeSettings


def port_available(host: str, port: int) -> bool:
    try:
        family, _type, _proto, _canon, sockaddr = socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )[0]
    except (socket.gaierror, IndexError):
        return False
    with socket.socket(family, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(sockaddr)
        except OSError:
            return False
    return True


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from tuppence.app import create_app

    try:
        data_dir = resolve_data_dir(args.data_dir)
    except DataDirError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings = RuntimeSettings.for_mode(
        args.mode, data_dir=data_dir, host=args.host, port=args.port
    )
    if not port_available(settings.host, settings.port):
        print(
            f"Port {settings.port} is already in use. "
            f"Try another with --port, e.g. --port {settings.port + 1}.",
            file=sys.stderr,
        )
        return 2
    shown_host = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host  # noqa: S104
    url = f"http://{shown_host}:{settings.port}/"
    print(f"Tuppence {__version__} running at {url}  (data: {data_dir})")
    if settings.mode == "local" and not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")
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
    sys.exit(main())
