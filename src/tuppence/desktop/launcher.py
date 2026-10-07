"""Desktop shell: run the app on a random loopback port inside a native window."""

from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

import httpx
import uvicorn

from tuppence import __version__
from tuppence.paths import DataDirError, resolve_data_dir
from tuppence.settings import RuntimeSettings


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def new_launch_token() -> str:
    return secrets.token_urlsafe(32)


class ServerThread:
    def __init__(self, app: object, host: str, port: int) -> None:
        self.host, self.port = host, port
        config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="on")  # type: ignore[arg-type]
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, name="tuppence-server", daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        self._thread.start()

    def wait_until_healthy(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if httpx.get(self.url + "health", timeout=1.0).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise TimeoutError(f"Tuppence didn't start within {timeout:.0f}s")

    def stop(self, timeout: float = 5.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout)


def open_webview_window(url: str) -> bool:
    try:
        import webview  # type: ignore[import-not-found,unused-ignore]
    except Exception:  # noqa: BLE001 - any import failure means "no GUI available"
        return False
    try:
        webview.create_window("Tuppence", url, width=1280, height=820, min_size=(900, 600))
        webview.start()
    except Exception:  # noqa: BLE001 - missing GTK/WebView2 etc.
        return False
    return True


def _wait_forever() -> None:
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def run_desktop(
    data_dir: str | None = None,
    *,
    smoke: bool = False,
    smoke_out: str | None = None,
    open_window: Callable[[str], bool] | None = None,
    open_browser: Callable[[str], object] | None = None,
    wait_forever: Callable[[], None] | None = None,
) -> int:
    from tuppence.app import create_app

    try:
        root = resolve_data_dir(data_dir)
    except DataDirError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    port = find_free_port()
    settings = RuntimeSettings.for_mode(
        "desktop", data_dir=root, port=port, launch_token=new_launch_token()
    )
    server = ServerThread(create_app(settings), "127.0.0.1", port)
    server.start()
    try:
        server.wait_until_healthy()
        if smoke:
            result = {"ok": True, "url": server.url, "version": __version__, "mode": "desktop"}
            text = json.dumps(result)
            if smoke_out:
                Path(smoke_out).write_text(text, encoding="utf-8")
            print(text)
            return 0
        if not (open_window or open_webview_window)(server.url):
            print(f"No desktop window available; opening Tuppence in your browser at {server.url}")
            (open_browser or webbrowser.open)(server.url)
            (wait_forever or _wait_forever)()
        return 0
    finally:
        server.stop()
