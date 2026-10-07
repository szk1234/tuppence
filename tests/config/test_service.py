import pytest

from tuppence.config.service import ConfigService, deep_merge
from tuppence.core.db import Database
from tuppence.core.errors import InputError
from tuppence.core.migrate import migrate
from tuppence.core.records import NotFound, VersionConflict
from tuppence.core.settings_store import SettingsStore

EXPECTED = {
    "categoriser",
    "transfer_matcher",
    "commitments",
    "backlog_sweep",
    "researcher",
    "question_planner",
    "purpose_analyst",
    "life_events",
    "learner",
    "linter",
    "skill_runner",
    "report_writer",
    "coach",
}


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    store = SettingsStore(db)
    user_dir = tmp_path / "config"
    (user_dir / "agents").mkdir(parents=True)
    return ConfigService(db, store, user_dir), store, user_dir


def test_deep_merge():
    assert deep_merge({"a": {"b": 1, "c": 2}, "l": [1]}, {"a": {"b": 9}, "l": [2]}) == {
        "a": {"b": 9, "c": 2},
        "l": [2],
    }


def test_all_default_agents_load_with_spec_values(env):
    cfg, _, _ = env
    assert set(cfg.agent_names()) == EXPECTED
    r = cfg.get("researcher")
    assert (
        r.limits["max_merchants_per_run"],
        r.limits["max_tool_calls_per_merchant"],
        r.limits["max_page_fetches_per_merchant"],
    ) == (20, 5, 2)
    p = cfg.get("purpose_analyst")
    assert (p.limits["max_depth"], p.limits["max_open_conversations"]) == (5, 3)
    q = cfg.get("question_planner")
    assert q.questions is not None and q.questions.max_open == 5
    c = cfg.get("coach")
    assert (
        c.limits["max_tool_calls_per_turn"] == 8
        and c.budgets.max_seconds == 60
        and c.limits["local_max_seconds"] == 180
    )
    assert cfg.get("backlog_sweep").thresholds["revisit_below_confidence"] == 0.7


def test_preset_layer(env):
    cfg, store, _ = env
    store.set("config.preset", "frugal", expected_version=0)
    assert cfg.get("researcher").enabled is False
    assert cfg.get("coach").limits["max_tool_calls_per_turn"] == 4
    assert cfg.get("coach").limits["max_rows_per_tool_result"] == 50  # untouched keys survive


def test_user_file_layer_and_ui_layer_precedence(env):
    cfg, _, user_dir = env
    (user_dir / "agents" / "researcher.toml").write_text(
        "[limits]\nmax_merchants_per_run = 30\n", encoding="utf-8"
    )
    assert cfg.get("researcher").limits["max_merchants_per_run"] == 30
    view = cfg.set_override(
        "researcher", {"limits": {"max_merchants_per_run": 12}}, expected_version=0
    )
    assert view.manifest.limits["max_merchants_per_run"] == 12
    assert view.overridden == ["limits.max_merchants_per_run"] and view.version == 1


def test_bad_user_file_is_reported_and_ignored(env):
    cfg, _, user_dir = env
    (user_dir / "agents" / "coach.toml").write_text("tone = 'shouty'\n", encoding="utf-8")
    view = cfg.view("coach")
    assert view.manifest.tone == "plain"
    assert view.user_file_error and "coach.toml" in view.user_file_error
    (user_dir / "agents" / "learner.toml").write_text("this is [not toml", encoding="utf-8")
    assert cfg.view("learner").user_file_error


def test_invalid_override_rejected(env):
    cfg, _, _ = env
    with pytest.raises(InputError) as exc:
        cfg.set_override("coach", {"budgets": {"max_gbp": -5}}, expected_version=0)
    assert "budgets.max_gbp" in str(exc.value)
    with pytest.raises(InputError):
        cfg.set_override("coach", {"name": "renamed"}, expected_version=0)


def test_reset_field_and_versions(env):
    cfg, _, _ = env
    cfg.set_override("coach", {"tone": "detailed", "budgets": {"max_gbp": 0.5}}, expected_version=0)
    with pytest.raises(VersionConflict):
        cfg.set_override("coach", {"tone": "plain"}, expected_version=0)
    v = cfg.reset_field("coach", "budgets.max_gbp", expected_version=1)
    assert v.manifest.budgets.max_gbp == 0.25 and v.overridden == ["tone"] and v.version == 2


def test_unknown_agent(env):
    cfg, _, _ = env
    with pytest.raises(NotFound):
        cfg.get("nope")


def test_export(env):
    cfg, _, _ = env
    out = cfg.export()
    assert out["preset"] == "balanced" and set(out["agents"]) == EXPECTED
