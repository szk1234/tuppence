import socket

import pytest

from tuppence import __version__
from tuppence.cli import main, port_available


def test_version_prints(capsys):
    assert main(["version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_port_in_use_exits_2_with_hint(tmp_path, capsys):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert not port_available("127.0.0.1", port)
        rc = main(["serve", "--port", str(port), "--no-browser", "--data-dir", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert f"Port {port} is already in use" in err
    assert "--port" in err


def test_unwritable_data_dir_exits_2(tmp_path, capsys, monkeypatch):
    from tuppence import cli
    from tuppence.paths import DataDirError

    def boom(_override=None):
        raise DataDirError(
            "Tuppence can't write to its data folder /nope. "
            "Set TUPPENCE_DATA_DIR to a writable folder."
        )

    monkeypatch.setattr(cli, "resolve_data_dir", boom)
    rc = main(["serve", "--no-browser", "--data-dir", "/nope"])
    assert rc == 2
    assert "TUPPENCE_DATA_DIR" in capsys.readouterr().err


def _ipv6_loopback_usable() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
    except OSError:
        return False
    return True


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(not _ipv6_loopback_usable(), reason="IPv6 loopback unavailable")
def test_port_available_ipv6_loopback():
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
        s.bind(("::1", 0))
        port = s.getsockname()[1]
    assert port_available("::1", port)


def test_port_available_after_time_wait():
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    conn.close()  # server closes first -> TIME_WAIT on the server side
    client.close()
    server.close()
    assert port_available("127.0.0.1", port)


def test_port_available_false_for_unresolvable_host():
    assert not port_available("no-such-host.invalid", 8040)


def test_local_serve_prints_and_opens_launch_url(tmp_path, capsys, monkeypatch):
    import uvicorn

    from tuppence import cli

    opened = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert main(["serve", "--port", str(port), "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"http://127.0.0.1:{port}/auth/launch?token=" in out
    assert len(opened) == 1 and opened[0] in out


def test_server_mode_prints_plain_url(tmp_path, capsys, monkeypatch):
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    args = ["serve", "--mode", "server", "--port", str(port), "--data-dir", str(tmp_path)]
    assert main(args) == 0
    assert "auth/launch" not in capsys.readouterr().out


def _serve_capturing_app(monkeypatch, tmp_path, *extra):
    import uvicorn

    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **k: apps.append(app))
    port = _free_port()
    args = ["serve", "--mode", "server", "--port", str(port), "--data-dir", str(tmp_path), *extra]
    assert main(args) == 0
    return apps[0].state.settings


def test_secure_cookies_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("TUPPENCE_SECURE_COOKIES", raising=False)
    assert _serve_capturing_app(monkeypatch, tmp_path).secure_cookies is False


def test_secure_cookies_flag_turns_them_on(tmp_path, monkeypatch):
    monkeypatch.delenv("TUPPENCE_SECURE_COOKIES", raising=False)
    settings = _serve_capturing_app(monkeypatch, tmp_path, "--secure-cookies")
    assert settings.secure_cookies is True


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("0", False), ("", False)])
def test_secure_cookies_env_var(tmp_path, monkeypatch, value, expected):
    monkeypatch.setenv("TUPPENCE_SECURE_COOKIES", value)
    assert _serve_capturing_app(monkeypatch, tmp_path).secure_cookies is expected
