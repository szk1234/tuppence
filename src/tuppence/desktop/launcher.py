"""Desktop shell: run the app on a random loopback port inside a native window."""

from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from tuppence import __version__
from tuppence.paths import (
    DataDirError,
    InstanceLocked,
    InstanceLockError,
    acquire_instance_lock,
    resolve_data_dir,
)
from tuppence.settings import RuntimeSettings
from tuppence.startup import STARTUP_ERRORS, clear_report, report, startup_failure


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def new_launch_token() -> str:
    return secrets.token_urlsafe(32)


def bind_loopback_socket(host: str = "127.0.0.1") -> socket.socket:
    """Bind and listen now, so the port is never released before uvicorn serves on it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Stop other local processes from binding the same port (port hijack).
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, 0))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


class ServerThread:
    def __init__(
        self, app: object, host: str, port: int, sock: socket.socket | None = None
    ) -> None:
        self.host, self.port = host, port
        self._sock = sock
        config = uvicorn.Config(
            app,  # type: ignore[arg-type]
            host=host,
            port=port,
            log_level="warning",
            lifespan="on",
            log_config=None,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, name="tuppence-server", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def _run(self) -> None:
        self._server.run(sockets=[self._sock] if self._sock is not None else None)

    def start(self) -> None:
        self._thread.start()

    def wait_until_healthy(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        with httpx.Client(trust_env=False, timeout=1.0) as client:
            while time.monotonic() < deadline:
                if not self._thread.is_alive():
                    raise RuntimeError("Tuppence's local server stopped while starting")
                try:
                    if client.get(self.url + "health").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        raise TimeoutError(f"Tuppence didn't start within {timeout:.0f}s")

    def stop(self, timeout: float = 5.0) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout)
        if self._sock is not None:
            self._sock.close()


def _allow_downloads(webview: Any) -> None:
    """Let "Export settings" save its file: pywebview cancels downloads by default."""
    settings = getattr(webview, "settings", None)
    if isinstance(settings, MutableMapping) and "ALLOW_DOWNLOADS" in settings:
        settings["ALLOW_DOWNLOADS"] = True


def open_webview_window(url: str) -> bool:
    try:
        import webview  # type: ignore[import-not-found,unused-ignore]
    except Exception:  # noqa: BLE001 - any import failure means "no GUI available"
        return False
    try:
        _allow_downloads(webview)
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


def smoke_ocr(root: Path) -> int:
    """Read a tiny generated PNG in the sandbox, proving the frozen OCR path works: process
    spawn, the bundled models and onnxruntime. Returns how many text rows were found."""
    from PIL import Image, ImageDraw

    from tuppence.ingest.ocr import image_rows
    from tuppence.ingest.results import parse_image_rows
    from tuppence.ingest.sandbox import run_isolated

    image = Image.new("RGB", (480, 90), "white")
    ImageDraw.Draw(image).text((10, 20), "Greenbasket Stores 42.18", fill="black", font_size=32)
    path = root / "smoke-ocr.png"
    try:
        image.save(path, "PNG")
        rows = run_isolated(image_rows, str(path), timeout_s=90, parse=parse_image_rows).rows
    finally:
        path.unlink(missing_ok=True)
    if not rows:
        raise RuntimeError("The OCR smoke test found no text.")
    return len(rows)


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
        report(str(exc), None)  # the data folder is the problem: nowhere to leave a note
        return 2
    try:
        lock = acquire_instance_lock(root)
    except (InstanceLocked, InstanceLockError) as exc:
        report(str(exc), root)
        return 2
    try:
        sock = bind_loopback_socket()
        port = int(sock.getsockname()[1])
        token = new_launch_token()
        settings = RuntimeSettings.for_mode("desktop", data_dir=root, port=port, launch_token=token)
        try:
            app = create_app(settings)  # opens and migrates the database
        except STARTUP_ERRORS as exc:
            sock.close()
            report(startup_failure(root, exc), root)
            return 2
        server = ServerThread(app, "127.0.0.1", port, sock)
        server.start()
        try:
            try:
                server.wait_until_healthy()
            except (TimeoutError, RuntimeError) as exc:
                report(f"Tuppence could not start: {exc}", root)
                return 1
            clear_report(root)
            if smoke:
                result = {"ok": True, "url": server.url, "version": __version__, "mode": "desktop"}
                result["ocr_rows"] = smoke_ocr(root)
                text = json.dumps(result)
                if smoke_out:
                    Path(smoke_out).write_text(text, encoding="utf-8")
                print(text)
                return 0
            launch_url = f"{server.url}auth/launch?token={token}"
            if not (open_window or open_webview_window)(launch_url):
                print("No desktop window available; opening Tuppence in your browser.")
                (open_browser or webbrowser.open)(launch_url)
                (wait_forever or _wait_forever)()
            return 0
        finally:
            server.stop()
    finally:
        lock.release()
