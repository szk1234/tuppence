import sys
from pathlib import Path

import keyring
import pytest
from keyring.backends import fail

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(autouse=True)
def _no_real_keychain():
    """Never touch the developer's OS keychain from tests."""
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    yield
    keyring.set_keyring(previous)
