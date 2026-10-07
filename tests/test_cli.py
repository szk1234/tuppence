import socket

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
