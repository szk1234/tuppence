import os
import sys
from pathlib import Path

# No test may reach an external tracing endpoint, even one that switches tracing on to prove a
# guard works: tracing is off and LangSmith points at a closed local port for the whole session
# (set here, before anything imports tuppence, langgraph or langsmith).
os.environ.update(
    {
        "LANGSMITH_TRACING": "false",
        "LANGSMITH_TRACING_V2": "false",
        "LANGCHAIN_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
        "LANGSMITH_ENDPOINT": "http://127.0.0.1:9",
        "LANGCHAIN_ENDPOINT": "http://127.0.0.1:9",
    }
)
for _name in ("LANGSMITH_RUNS_ENDPOINTS", "LANGCHAIN_RUNS_ENDPOINTS"):
    os.environ.pop(_name, None)

import keyring  # noqa: E402
import pytest  # noqa: E402
from keyring.backends import fail  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _no_real_keychain():
    """Never touch the developer's OS keychain from tests."""
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    yield
    keyring.set_keyring(previous)
