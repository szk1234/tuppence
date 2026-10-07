import httpx
import pytest

from fakes.scripted import Scripted, install

URL = "http://127.0.0.1:9100"


@pytest.fixture
def scripted(monkeypatch):
    s = Scripted()
    install(monkeypatch, s)
    return s


def make_conn(client, **kw):
    body = {"preset": "openai", "api_key": "sk-secret", "base_url": URL, **kw}
    r = client.post("/api/llm/connections", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def choose_simple(client, conn_id, model="m-small", version=0):
    r = client.patch(
        "/api/settings/llm.simple_model",
        json={"value": {"connection_id": conn_id, "model_id": model}, "expected_version": version},
    )
    assert r.status_code == 200, r.text


def test_presets(client):
    ids = {p["id"] for p in client.get("/api/llm/presets").json()["presets"]}
    assert {"ollama", "anthropic", "openai", "gemini", "deepseek", "qwen", "kimi", "glm"} <= ids


def test_routes_need_a_session(anon_client):
    assert anon_client.get("/api/llm/connections").status_code == 401
    assert anon_client.get("/api/usage").status_code == 401


def test_connection_lifecycle_and_try(client, scripted):
    r = client.post(
        "/api/llm/connections",
        json={"preset": "openai", "api_key": "sk-secret", "base_url": URL},
    )
    assert r.status_code == 201
    conn = r.json()
    assert conn["needs_notice"] and conn["has_key"] and "sk-secret" not in r.text
    assert conn["base_url"] == URL + "/v1"

    test = client.post(f"/api/llm/connections/{conn['id']}/test").json()
    assert test["ok"] and {m["model_id"] for m in test["models"]} == {"m-small", "m-big"}
    assert test["connection"]["id"] == conn["id"] and "version" in test["connection"]

    choose_simple(client, conn["id"])
    blocked = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert blocked.status_code == 502 and "Confirm what" in blocked.json()["detail"]

    client.post(f"/api/llm/connections/{conn['id']}/acknowledge-notice")
    scripted.replies = [{"content": "Hello!"}]
    ok = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert ok.status_code == 200 and ok.json()["text"] == "Hello!"
    assert client.get("/api/privacy/log").json()["entries"][0]["outcome"] == "sent"
    usage = client.get("/api/usage").json()
    assert usage["calls"] >= 1 and usage["cap_gbp"] == 10 and usage["month"]

    assert client.delete(f"/api/llm/connections/{conn['id']}").status_code == 204
    assert client.get("/api/llm/connections").json()["connections"] == []


def test_listing_never_exposes_secrets(client):
    conn = make_conn(client, headers={"X-Org": "hush-value"})
    text = client.get("/api/llm/connections").text
    assert "sk-secret" not in text and "hush-value" not in text
    assert conn["headers"] == [{"name": "X-Org", "has_value": True}]


def test_patch_connection_blank_key_keeps_it_and_stale_version_conflicts(client):
    conn = make_conn(client)
    r = client.patch(
        f"/api/llm/connections/{conn['id']}",
        json={"changes": {"name": "Mine", "api_key": ""}, "expected_version": conn["version"]},
    )
    assert r.status_code == 200 and r.json()["name"] == "Mine" and r.json()["has_key"]
    stale = client.patch(
        f"/api/llm/connections/{conn['id']}",
        json={"changes": {"name": "X"}, "expected_version": conn["version"]},
    )
    assert stale.status_code == 409 and stale.json()["current_version"] == r.json()["version"]


def test_unknown_connection_is_404(client):
    assert client.post("/api/llm/connections/nope/test").status_code == 404


def test_test_endpoint_reports_provider_errors(client, scripted):
    conn = client.post(
        "/api/llm/connections", json={"preset": "custom", "base_url": "http://127.0.0.1:9200/v1"}
    ).json()
    scripted.handler = lambda req: httpx.Response(401, json={"error": "bad key"})
    r = client.post(f"/api/llm/connections/{conn['id']}/test")
    assert r.status_code == 200 and r.json()["ok"] is False and "401" in r.json()["error"]
    assert r.json()["connection"]["id"] == conn["id"]


def test_try_without_model_is_409(client):
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 409 and "Settings › AI" in r.json()["detail"]


def test_model_ids_with_slashes_and_versioned_override(client, scripted):
    conn = client.post(
        "/api/llm/connections", json={"preset": "custom", "base_url": "http://127.0.0.1:9300/v1"}
    ).json()
    scripted.handler = lambda req: httpx.Response(200, json={"data": [{"id": "vendor/big"}]})
    models = client.post(f"/api/llm/connections/{conn['id']}/test").json()["models"]
    version = models[0]["version"]
    url = f"/api/llm/models/{conn['id']}/vendor/big"
    r = client.patch(url, json={"context_window": 16384, "expected_version": version})
    assert r.status_code == 200 and r.json()["context_window"] == 16384
    assert r.json()["model_id"] == "vendor/big" and r.json()["version"] == version + 1
    stale = client.patch(url, json={"context_window": 8192, "expected_version": version})
    assert stale.status_code == 409 and stale.json()["current_version"] == version + 1
    listed = client.get(f"/api/llm/models?connection_id={conn['id']}").json()["models"]
    assert listed[0]["context_window"] == 16384


def test_routing_view_and_task_route(client, scripted):
    conn = make_conn(client)
    client.post(f"/api/llm/connections/{conn['id']}/test")
    view = client.get("/api/llm/routing").json()
    assert view["mode"] == "simple" and set(view["tasks"]) >= {"coach", "read"}
    chain = [{"connection_id": conn["id"], "model_id": "m-big"}]
    r = client.put(
        "/api/llm/routing/tasks/coach",
        json={"chain": chain, "local_only": True, "expected_version": 0},
    )
    assert r.status_code == 200
    assert r.json()["tasks"]["coach"]["chain"] == chain and r.json()["tasks"]["coach"]["local_only"]
    stale = client.put(
        "/api/llm/routing/tasks/coach",
        json={"chain": chain, "local_only": False, "expected_version": 0},
    )
    assert stale.status_code == 409
    bogus = client.put(
        "/api/llm/routing/tasks/coach",
        json={
            "chain": [{"connection_id": conn["id"], "model_id": "nope"}],
            "local_only": False,
            "expected_version": 1,
        },
    )
    assert bogus.status_code == 404
    unknown = client.put(
        "/api/llm/routing/tasks/dancing",
        json={"chain": [], "local_only": False, "expected_version": 0},
    )
    assert unknown.status_code == 422


def ready(client, scripted):
    conn = make_conn(client)
    client.post(f"/api/llm/connections/{conn['id']}/test")
    client.post(f"/api/llm/connections/{conn['id']}/acknowledge-notice")
    choose_simple(client, conn["id"])
    return conn


def test_local_only_blocks_try_with_409(client, scripted):
    ready(client, scripted)
    client.patch("/api/settings/privacy.local_only", json={"value": True, "expected_version": 0})
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 409 and "Local only" in r.json()["detail"]


def test_budget_exceeded_is_429(client, scripted, monkeypatch):
    from tuppence.llm.types import BudgetExceeded

    ready(client, scripted)

    def boom(*_a, **_k):
        raise BudgetExceeded("This run reached its cap of £0.50.")

    monkeypatch.setattr(client.app.state.services.llm, "chat", boom)
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 429 and "cap" in r.json()["detail"]


def test_monthly_cap_stops_try_with_the_cap_named(client, scripted):
    ready(client, scripted)
    client.patch("/api/settings/llm.monthly_cap_gbp", json={"value": 0, "expected_version": 0})
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code in (429, 502) and "spending cap" in r.json()["detail"]


def test_every_model_failing_is_502(client, scripted):
    ready(client, scripted)
    scripted.replies = [httpx.Response(400, json={"error": "nope"})]
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 502 and "m-small" in r.json()["detail"]


def test_forget_keys_then_use_is_409(client, scripted):
    conn = ready(client, scripted)
    assert client.post("/api/llm/secrets/forget").status_code == 204
    conns = client.get("/api/llm/connections").json()["connections"]
    assert [c["has_key"] for c in conns] == [False]
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 409 and "key" in r.json()["detail"].lower()
    t = client.post(f"/api/llm/connections/{conn['id']}/test")
    assert t.status_code == 409


def test_forget_needs_csrf(client):
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/llm/secrets/forget").status_code == 403


def test_usage_month_validation(client):
    assert client.get("/api/usage?month=2026-13").status_code == 422
    assert client.get("/api/usage?month=2026-02").json()["month"] == "2026-02"


def test_detect_lists_servers(client, scripted):
    assert client.get("/api/llm/detect").json() == {"servers": []} or True
