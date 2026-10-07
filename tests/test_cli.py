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


def _serve_server(tmp_path):
    return main(
        ["serve", "--mode", "server", "--port", str(_free_port()), "--data-dir", str(tmp_path)]
    )


def _assert_friendly_failure(rc, err, data_dir):
    assert rc == 2
    assert "Traceback" not in err
    assert "Tuppence couldn't start" in err
    assert str(data_dir / "backups") in err


def test_serve_with_corrupt_database_exits_2_naming_backups(tmp_path, capsys, monkeypatch):
    import uvicorn

    from tuppence.paths import acquire_instance_lock

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("must not serve"))
    (tmp_path / "tuppence.db").write_bytes(b"this is not a database, " * 200)
    rc = _serve_server(tmp_path)
    out = capsys.readouterr()
    _assert_friendly_failure(rc, out.err, tmp_path)
    assert "running at" not in out.out
    acquire_instance_lock(tmp_path).release()  # the lock was released


@pytest.mark.parametrize("kind", ["database", "migration", "oserror"])
def test_serve_startup_errors_are_friendly(tmp_path, capsys, monkeypatch, kind):
    import sqlite3

    import uvicorn

    from tuppence.app import services
    from tuppence.core.migrate import MigrationError

    errors = {
        "database": sqlite3.DatabaseError("database disk image is malformed"),
        "migration": MigrationError("Migration 0005_x failed: boom"),
        "oserror": OSError(28, "No space left on device"),
    }

    def broken(*_a, **_k):
        raise errors[kind]

    monkeypatch.setattr(services, "migrate", broken)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("must not serve"))
    _assert_friendly_failure(_serve_server(tmp_path), capsys.readouterr().err, tmp_path)


def test_serve_refuses_data_from_a_newer_version(tmp_path, capsys, monkeypatch):
    import uvicorn

    from tuppence.core.db import Database
    from tuppence.core.migrate import migrate

    db = Database(tmp_path / "tuppence.db")
    migrate(db, tmp_path / "backups")
    with db.transaction() as conn:
        conn.execute("INSERT INTO schema_migrations VALUES ('9999_future', 'x')")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("must not serve"))
    rc = _serve_server(tmp_path)
    err = capsys.readouterr().err
    _assert_friendly_failure(rc, err, tmp_path)
    assert "newer version of Tuppence" in err
