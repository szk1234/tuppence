import hashlib
import os
import stat

import pytest

from tuppence.ingest.files import StatementFiles


def test_saved_once_under_its_hash(tmp_path):
    files = StatementFiles(tmp_path / "files" / "statements")
    sha, path = files.save(b"Date,Amount\n", "csv")
    assert sha == hashlib.sha256(b"Date,Amount\n").hexdigest()
    assert (
        path == tmp_path / "files" / "statements" / f"{sha}.csv"
        and path.read_bytes() == b"Date,Amount\n"
    )
    assert files.save(b"Date,Amount\n", "csv") == (sha, path)
    files.delete(sha, "csv")
    assert not path.exists()
    files.delete(sha, "csv")  # already gone: no error


@pytest.mark.parametrize(
    "sha,ext", [("../../etc/passwd", "csv"), ("a" * 64, "exe"), ("A" * 64, "csv")]
)
def test_path_for_refuses_odd_names(tmp_path, sha, ext):
    with pytest.raises(ValueError):
        StatementFiles(tmp_path).path_for(sha, ext)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_folder_is_private_and_files_are_owner_only(tmp_path):
    root = tmp_path / "files" / "statements"
    _, path = StatementFiles(root).save(b"Date,Amount\n", "csv")
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
