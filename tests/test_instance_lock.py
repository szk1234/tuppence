import subprocess
import sys
import time

from tuppence.cli import main
from tuppence.paths import InstanceLocked, acquire_instance_lock

HOLDER = (
    "import sys, time\n"
    "from tuppence.paths import acquire_instance_lock\n"
    "acquire_instance_lock(sys.argv[1]); print('locked', flush=True); time.sleep(60)\n"
)


def _hold(path):
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", HOLDER, str(path)], stdout=subprocess.PIPE, text=True
    )
    assert proc.stdout.readline().strip() == "locked"
    return proc


def test_second_serve_is_refused(tmp_path, capsys):
    proc = _hold(tmp_path)
    try:
        code = main(["serve", "--mode", "server", "--data-dir", str(tmp_path), "--port", "0"])
    finally:
        proc.kill()
        proc.wait()
    assert code == 2
    assert "Tuppence is already running with this data folder." in capsys.readouterr().err


def test_lock_released_when_holder_exits(tmp_path):
    proc = _hold(tmp_path)
    try:
        try:
            acquire_instance_lock(tmp_path)
            raise AssertionError("expected the lock to be held")
        except InstanceLocked:
            pass
    finally:
        proc.kill()
        proc.wait()
    deadline = time.monotonic() + 5
    while True:
        try:
            lock = acquire_instance_lock(tmp_path)
            break
        except InstanceLocked:
            assert time.monotonic() < deadline
            time.sleep(0.05)
    lock.release()


def test_different_folders_do_not_conflict(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    la, lb = acquire_instance_lock(a), acquire_instance_lock(b)
    la.release()
    lb.release()
