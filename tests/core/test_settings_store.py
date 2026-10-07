import pytest

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.core.records import VersionConflict
from tuppence.core.settings_store import SettingInvalid, SettingsStore


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return SettingsStore(db)


def test_defaults_have_version_zero(store):
    e = store.entry("privacy.local_only")
    assert e.value is False and e.version == 0 and e.default is False
    assert store.get("privacy.live_market_data") is True
    assert store.get("config.preset") == "balanced"


def test_set_then_get_and_versioning(store):
    e = store.set("privacy.local_only", True, expected_version=0)
    assert e.value is True and e.version == 1
    e2 = store.set("privacy.local_only", False, expected_version=1)
    assert e2.version == 2 and store.get("privacy.local_only") is False


def test_stale_write_conflicts(store):
    store.set("privacy.local_only", True, expected_version=0)
    with pytest.raises(VersionConflict) as exc:
        store.set("privacy.local_only", False, expected_version=0)
    assert exc.value.current == 1


def test_validation(store):
    with pytest.raises(SettingInvalid):
        store.set("config.preset", "turbo", expected_version=0)
    with pytest.raises(SettingInvalid):
        store.set("llm.monthly_cap_gbp", -1, expected_version=0)
    with pytest.raises(KeyError):
        store.get("no.such.key")


def test_all_lists_every_defined_setting(store):
    keys = {e.key for e in store.all()}
    assert {
        "privacy.local_only",
        "privacy.pseudonymise",
        "config.preset",
        "llm.monthly_cap_gbp",
    } <= keys
