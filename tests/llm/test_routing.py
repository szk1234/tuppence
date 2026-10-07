import pytest

from tuppence.llm.types import NoModelConfigured


def test_no_model_configured(env):
    services, _ = env
    with pytest.raises(NoModelConfigured):
        services.router.chain_for("coach")


def test_simple_mode_and_advanced_chain(env):
    services, _ = env
    local = services.connections.create("custom", base_url="http://127.0.0.1:9000/v1")
    services.connections.test(local.id)
    services.settings.set(
        "llm.simple_model", {"connection_id": local.id, "model_id": "m-small"}, expected_version=0
    )
    [(conn, model)] = services.router.chain_for("coach")
    assert conn.id == local.id and model.model_id == "m-small"

    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task(
        "coach",
        [
            {"connection_id": local.id, "model_id": "m-big"},
            {"connection_id": local.id, "model_id": "m-small"},
        ],
        local_only=False,
        expected_version=0,
    )
    assert [m.model_id for _, m in services.router.chain_for("coach")] == ["m-big", "m-small"]
    # tasks without their own chain fall back to the simple model
    assert [m.model_id for _, m in services.router.chain_for("report")] == ["m-small"]


def test_local_only_route_filters_cloud(env):
    services, _ = env
    cloud = services.connections.create("openai", api_key="k", base_url="http://127.0.0.1:9001/v1")
    services.connections.test(cloud.id)
    services.settings.set("llm.mode", "advanced", expected_version=0)
    services.router.set_task(
        "read",
        [{"connection_id": cloud.id, "model_id": "m-small"}],
        local_only=True,
        expected_version=0,
    )
    with pytest.raises(NoModelConfigured):
        services.router.chain_for("read")
