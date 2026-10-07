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
        assert httpx.get(server.url + "health").json()["mode"] == "desktop"
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
