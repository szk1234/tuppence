import os
import sys

import pytest

from tuppence.paths import DataDirError, DataPaths, resolve_data_dir


def test_override_wins_and_is_created(tmp_path):
    target = tmp_path / "Zoë Smith" / "App Data"
    path = resolve_data_dir(target)
    assert path == target.resolve()
    assert path.is_dir()


def test_env_var_used_when_no_override(tmp_path, monkeypatch):
    monkeypatch.setenv("TUPPENCE_DATA_DIR", str(tmp_path / "envdir"))
    assert resolve_data_dir() == (tmp_path / "envdir").resolve()


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX non-root only")
def test_unwritable_dir_raises_clear_error(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(DataDirError) as exc:
            resolve_data_dir(locked / "inner")
        message = str(exc.value)
        assert "TUPPENCE_DATA_DIR" in message
        assert "locked" in message
    finally:
        locked.chmod(0o700)


def test_data_paths_layout(tmp_path):
    paths = DataPaths(tmp_path).ensure()
    assert paths.db == tmp_path / "tuppence.db"
    assert paths.checkpoints_db == tmp_path / "checkpoints.db"
    for d in (paths.files, paths.backups, paths.config):
        assert d.is_dir()
