import json
import socket

import httpx

from tuppence.desktop import launcher


def test_find_free_port_is_bindable():
    port = launcher.find_free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_launch_tokens_are_long_and_unique():
    a, b = launcher.new_launch_token(), launcher.new_launch_token()
    assert a != b and len(a) >= 32


def test_server_thread_serves_health(tmp_path):
    from tuppence.app import create_app
    from tuppence.settings import RuntimeSettings

    port = launcher.find_free_port()
    settings = RuntimeSettings.for_mode("desktop", data_dir=tmp_path, port=port)
    server = launcher.ServerThread(create_app(settings), "127.0.0.1", port)
    server.start()
    try:
        server.wait_until_healthy(15)
        assert httpx.get(server.url + "health", trust_env=False).json()["mode"] == "desktop"
    finally:
        server.stop()


def test_smoke_mode_writes_result_and_exits(tmp_path):
    out = tmp_path / "smoke.json"
    rc = launcher.run_desktop(data_dir=str(tmp_path / "data"), smoke=True, smoke_out=str(out))
    assert rc == 0
    result = json.loads(out.read_text())
    assert result["ok"] is True and result["mode"] == "desktop"
    assert result["url"].startswith("http://127.0.0.1:")


def test_falls_back_to_browser_when_no_gui(tmp_path):
    opened = []
    rc = launcher.run_desktop(
        data_dir=str(tmp_path / "data"),
        open_window=lambda url: False,
        open_browser=lambda url: opened.append(url),
        wait_forever=lambda: None,
    )
    assert rc == 0
    assert len(opened) == 1 and opened[0].startswith("http://127.0.0.1:")
    assert "/auth/launch?token=" in opened[0]


def test_smoke_reports_prebound_port(tmp_path, monkeypatch):
    bound = []
    real = launcher.bind_loopback_socket

    def spy(*a, **k):
        s = real(*a, **k)
        bound.append(s.getsockname()[1])
        return s

    monkeypatch.setattr(launcher, "bind_loopback_socket", spy)
    out = tmp_path / "s.json"
    assert launcher.run_desktop(data_dir=str(tmp_path / "d"), smoke=True, smoke_out=str(out)) == 0
    assert json.loads(out.read_text())["url"] == f"http://127.0.0.1:{bound[0]}/"


def test_wait_until_healthy_fails_fast_when_thread_dies():
    import time

    class Boom:
        async def __call__(self, scope, receive, send):
            raise RuntimeError("startup boom")

    server = launcher.ServerThread(Boom(), "127.0.0.1", launcher.find_free_port())
    # Make the thread exit immediately.
    server._run = lambda: None  # type: ignore[method-assign]
    server._thread = launcher.threading.Thread(target=server._run, daemon=True)
    server.start()
    start = time.monotonic()
    import pytest

    with pytest.raises(RuntimeError, match="stopped while starting"):
        server.wait_until_healthy(15)
    assert time.monotonic() - start < 3


def test_health_check_ignores_proxy_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    test_server_thread_serves_health(tmp_path)


def test_run_desktop_reports_startup_failure(tmp_path, monkeypatch, capsys):
    def dead(self, timeout=20.0):
        raise RuntimeError("Tuppence's local server stopped while starting")

    monkeypatch.setattr(launcher.ServerThread, "wait_until_healthy", dead)
    assert launcher.run_desktop(data_dir=str(tmp_path / "d"), smoke=True) == 1
    assert "could not start" in capsys.readouterr().err


def test_entry_ensure_std_streams(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "desktop" / "entry.py"
    spec = importlib.util.spec_from_file_location("tuppence_entry", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    mod.ensure_std_streams()
    sys.stdout.write("x")
    sys.stderr.write("x")
    sys.stdout.close()
    sys.stderr.close()


def test_desktop_launch_url_signs_in_once(tmp_path):
    seen = []

    def fake_window(url):
        with httpx.Client(trust_env=False, follow_redirects=False) as c:
            first = c.get(url)
            seen.append(
                (first.status_code, c.get(url.split("auth/")[0] + "api/settings").status_code)
            )
            seen.append(httpx.get(url, trust_env=False, follow_redirects=False).status_code)
        return True

    assert launcher.run_desktop(data_dir=str(tmp_path / "data"), open_window=fake_window) == 0
    assert seen == [(303, 200), 403]


def _corrupt(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "tuppence.db").write_bytes(b"not a database at all " * 200)


def test_run_desktop_corrupt_database_is_friendly(tmp_path, capsys):
    root = tmp_path / "data"
    _corrupt(root)
    assert launcher.run_desktop(data_dir=str(root), smoke=True) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and str(root / "backups") in err
    assert not (root / "startup-error.txt").exists()  # console run: stderr is enough


def _windowed(monkeypatch):
    import os
    import sys

    sink = open(os.devnull, "w")  # noqa: SIM115 - what desktop/entry.py does
    monkeypatch.setattr(sys, "stderr", sink)
    return sink


def test_windowed_startup_failure_writes_error_file(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _corrupt(root)
    sink = _windowed(monkeypatch)
    try:
        assert launcher.run_desktop(data_dir=str(root), smoke=True) == 2
    finally:
        sink.close()
    text = (root / "startup-error.txt").read_text(encoding="utf-8")
    assert "Tuppence couldn't start" in text and str(root / "backups") in text


def test_windowed_already_running_writes_error_file(tmp_path, monkeypatch):
    from tuppence.paths import acquire_instance_lock

    root = tmp_path / "data"
    root.mkdir()
    held = acquire_instance_lock(root)
    sink = _windowed(monkeypatch)
    try:
        assert launcher.run_desktop(data_dir=str(root), smoke=True) == 2
    finally:
        sink.close()
        held.release()
    assert "already running" in (root / "startup-error.txt").read_text(encoding="utf-8")


def test_successful_start_removes_stale_error_file(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    (root / "startup-error.txt").write_text("old problem", encoding="utf-8")
    assert launcher.run_desktop(data_dir=str(root), smoke=True) == 0
    assert not (root / "startup-error.txt").exists()


def test_webview_window_allows_downloads(monkeypatch):
    import sys
    import types

    class Settings(dict):
        def __setitem__(self, key, value):
            if key not in self:
                raise KeyError(key)
            super().__setitem__(key, value)

    fake = types.ModuleType("webview")
    fake.settings = Settings(ALLOW_DOWNLOADS=False)
    calls = []
    fake.create_window = lambda *a, **k: calls.append("window")
    fake.start = lambda *a, **k: calls.append("start")
    monkeypatch.setitem(sys.modules, "webview", fake)
    assert launcher.open_webview_window("http://127.0.0.1:1/") is True
    assert fake.settings["ALLOW_DOWNLOADS"] is True and calls == ["window", "start"]


def test_webview_window_without_download_setting_still_opens(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("webview")
    fake.create_window = lambda *a, **k: None
    fake.start = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "webview", fake)
    assert launcher.open_webview_window("http://127.0.0.1:1/") is True
