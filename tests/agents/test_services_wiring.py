import pytest

from tuppence.app import services as services_module
from tuppence.app.services import build_services, model_window
from tuppence.ingest.handoff import ANALYSIS_JOB, ANALYSIS_SCOPE
from tuppence.llm.types import AllModelsBlocked, NoModelConfigured
from tuppence.settings import RuntimeSettings


def test_services_wire_the_understanding_team(tmp_path):
    services = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    try:
        assert services.categories.tree().usable("food.groceries")
        assert any(r.id == "seed-council-tax" for r in services.rules.list())
        assert "analysis" in services.worker.handlers
        assert services.worker.handlers[ANALYSIS_JOB] == services.analysis.handle_job
        merge = services.queue.merges["analysis"]
        assert merge({"statement_ids": ["s_1"]}, {"reasons": ["correction"]}) == {
            "statement_ids": ["s_1"],
            "reasons": ["correction"],
        }
        assert services.analysis.runs() == [] and services.commitments.list() == []
        again = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
        again.checkpointer.conn.close()  # seeding twice is harmless
        assert len(services.categories.tree().roots()) == 20
    finally:
        services.checkpointer.conn.close()


def test_start_up_sweeps_analysis_checkpoints_after_the_ingest_sweep(tmp_path, monkeypatch):
    """The ingest sweep leaves analysis threads alone; the analysis sweep, right after it,
    drops those whose job has gone. Then the worker starts, and the daily light run is
    scheduled with the other periodic jobs."""
    services = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    calls = []

    def recorded(name, real):
        def call():
            calls.append(name)
            return real()

        return call

    monkeypatch.setattr(services.ingest, "sweep", recorded("ingest.sweep", services.ingest.sweep))
    monkeypatch.setattr(
        services.analysis, "sweep", recorded("analysis.sweep", services.analysis.sweep)
    )
    monkeypatch.setattr(services.worker, "start", lambda: calls.append("worker.start"))
    try:
        services.start()
        assert calls == ["ingest.sweep", "analysis.sweep", "worker.start"]
        periodic = {(p.kind, p.scope_key): p.interval_s for p in services.periodic}
        assert periodic[(ANALYSIS_JOB, ANALYSIS_SCOPE)] == 24 * 3600  # the daily light run
        assert isinstance(services.periodic[0], services_module.Periodic)
    finally:
        services.stop()


# --- the context window a specialist plans for ---------------------------------------------------


def _model(cenv, preset, base_url, window, **kw):
    services = cenv.services
    conn = services.connections.create(preset, base_url=base_url, **kw)
    services.connections.test(conn.id)
    if not conn.is_local:
        services.connections.acknowledge_notice(
            conn.id, expected_version=services.connections.get(conn.id).version
        )
    services.connections.set_context_window(conn.id, "m-small", window)
    return {"connection_id": conn.id, "model_id": "m-small"}


@pytest.fixture
def chain(cenv):
    """A cloud model (6,000 tokens) then a local one (8,192 tokens) for the categorise task."""
    services = cenv.services
    cloud = _model(cenv, "openai", "http://127.0.0.1:9100/v1", 6000, api_key="sk-x")
    local = _model(cenv, "custom", "http://127.0.0.1:9000/v1", 8192)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    return services, cloud, local


def test_no_model_is_no_model(cenv):
    with pytest.raises(NoModelConfigured):
        model_window(cenv.services.router, cenv.services.settings, "categorise")


def test_the_window_is_the_smallest_of_the_models_that_may_answer(chain):
    services, cloud, local = chain
    services.router.set_task("categorise", [cloud, local], False, expected_version=0)
    assert model_window(services.router, services.settings, "categorise") == 6000


def test_local_only_counts_only_local_models(chain):
    services, cloud, local = chain
    services.router.set_task("categorise", [cloud, local], False, expected_version=0)
    services.settings.set("privacy.local_only", True, expected_version=0)
    assert model_window(services.router, services.settings, "categorise") == 8192


def test_a_task_pinned_to_local_models_counts_only_those(chain):
    services, cloud, local = chain
    services.router.set_task("categorise", [cloud, local], True, expected_version=0)
    assert model_window(services.router, services.settings, "categorise") == 8192


def test_local_only_with_only_cloud_models_says_so_plainly(chain):
    services, cloud, _ = chain
    services.router.set_task("categorise", [cloud], False, expected_version=0)
    services.settings.set("privacy.local_only", True, expected_version=0)
    with pytest.raises(AllModelsBlocked, match="Local only is on") as caught:
        model_window(services.router, services.settings, "categorise")
    assert "Settings › AI" in str(caught.value)
