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
