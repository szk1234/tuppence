import sys
import time

import pytest

from ingest import sandbox_helpers as helpers
from tuppence.ingest.sandbox import SandboxFailed, SandboxTimeout, resident_mb, run_isolated


def test_returns_the_childs_result():
    assert run_isolated(helpers.add, 2, 3, timeout_s=30) == 5


def test_a_hanging_parser_is_stopped():
    started = time.monotonic()
    with pytest.raises(SandboxTimeout, match="longer than 1 seconds"):
        run_isolated(helpers.sleepy, 30, timeout_s=1)
    assert time.monotonic() - started < 10


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="memory is watched on Linux only")
def test_a_memory_hungry_parser_is_stopped():
    with pytest.raises(SandboxFailed, match="more memory"):
        run_isolated(helpers.greedy, timeout_s=60, memory_mb=200)


def test_errors_and_crashes_become_plain_messages():
    with pytest.raises(SandboxFailed, match=r"couldn't be read \(ValueError\)"):
        run_isolated(helpers.explode, timeout_s=30)
    with pytest.raises(SandboxFailed, match="stopped unexpectedly"):
        run_isolated(helpers.vanish, timeout_s=30)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_resident_memory_is_readable():
    import os

    assert resident_mb(os.getpid()) > 1


def test_library_error_text_never_reaches_the_message():
    with pytest.raises(SandboxFailed) as caught:
        run_isolated(helpers.explode, timeout_s=30)
    assert "bad table" not in str(caught.value)


def test_sandbox_errors_are_safe_to_show():
    from tuppence.core.errors import UserFacing, safe_error_text

    assert issubclass(SandboxFailed, UserFacing) and issubclass(SandboxTimeout, UserFacing)
    assert safe_error_text(SandboxFailed("plain words")) == "plain words"


def test_the_child_cannot_use_the_network():
    with pytest.raises(SandboxFailed, match=r"couldn't be read \(OSError\)"):
        run_isolated(helpers.connect_out, timeout_s=30)
    assert run_isolated(helpers.create_connection_blocked, timeout_s=30) is True
