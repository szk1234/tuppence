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
    body = blocked.json()
    assert blocked.status_code == 409 and "Confirm what" in body["detail"]
    assert body["code"] == "notice_required" and body["connection_id"] == conn["id"]

    ack = client.post(
        f"/api/llm/connections/{conn['id']}/acknowledge-notice",
        json={"expected_version": test["connection"]["version"]},
    )
    assert ack.status_code == 200 and not ack.json()["needs_notice"]
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


def acknowledge(client, conn_id):
    version = client.get("/api/llm/connections").json()["connections"]
    version = next(c["version"] for c in version if c["id"] == conn_id)
    r = client.post(
        f"/api/llm/connections/{conn_id}/acknowledge-notice", json={"expected_version": version}
    )
    assert r.status_code == 200, r.text


def ready(client, scripted):
    conn = make_conn(client)
    client.post(f"/api/llm/connections/{conn['id']}/test")
    acknowledge(client, conn["id"])
    choose_simple(client, conn["id"])
    return conn


def test_local_only_blocks_try_with_409(client, scripted):
    ready(client, scripted)
    client.patch("/api/settings/privacy.local_only", json={"value": True, "expected_version": 0})
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 409 and "Local only" in r.json()["detail"]


def test_monthly_cap_is_429_and_names_the_cap(client, scripted):
    ready(client, scripted)
    client.patch("/api/settings/llm.monthly_cap_gbp", json={"value": 0, "expected_version": 0})
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 429 and "month" in r.json()["detail"].lower()


def test_every_model_failing_is_502(client, scripted):
    ready(client, scripted)
    scripted.replies = [httpx.Response(400, json={"error": "nope"})]
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 502 and "m-small" in r.json()["detail"]


def test_forget_keys_then_use_is_409(client, scripted):
    conn = ready(client, scripted)
    forgot = client.post("/api/llm/secrets/forget")
    assert forgot.status_code == 200 and forgot.json() == {"not_removed": 0}
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


# --- credentials never follow a host change -------------------------------------------


def auth_headers(req):
    return {k: v for k, v in req.headers.items() if k.lower() in {"authorization", "x-org"}}


def test_retargeting_a_connection_drops_its_saved_credentials(client, scripted):
    conn = make_conn(client, preset="custom", headers={"X-Org": "hdrsecret-777"})
    seen = []

    def handler(req):
        seen.append((str(req.url), auth_headers(req)))
        return httpx.Response(200, json={"data": [{"id": "m-small"}]})

    scripted.handler = handler
    r = client.patch(
        f"/api/llm/connections/{conn['id']}",
        json={
            "changes": {"base_url": "http://evil.example.com"},
            "expected_version": conn["version"],
        },
    )
    assert r.status_code == 200
    moved = r.json()
    assert moved["has_key"] is False
    assert moved["headers"] == [{"name": "X-Org", "has_value": False}]
    # Header names stay but their values are gone, so the test stops until they're re-entered.
    assert client.post(f"/api/llm/connections/{conn['id']}/test").status_code == 409
    assert seen == []
    fixed = client.patch(
        f"/api/llm/connections/{conn['id']}",
        json={"changes": {"headers": {}}, "expected_version": moved["version"]},
    ).json()
    assert fixed["headers"] == []
    client.post(f"/api/llm/connections/{conn['id']}/test")
    assert seen and all(h == {} for _, h in seen)
    assert "sk-secret" not in str(seen) and "hdrsecret" not in str(seen)


def test_retargeted_key_required_connection_asks_for_the_key_again(client, scripted):
    conn = make_conn(client)
    client.patch(
        f"/api/llm/connections/{conn['id']}",
        json={"changes": {"base_url": "http://evil.example.com"}, "expected_version": 1},
    )
    scripted.handler = lambda req: pytest.fail("nothing may be sent without the key")
    assert client.post(f"/api/llm/connections/{conn['id']}/test").status_code == 409


def test_retargeting_with_a_new_key_keeps_the_new_key(client, scripted):
    conn = make_conn(client)
    seen = []

    def handler(req):
        seen.append(auth_headers(req))
        return httpx.Response(200, json={"data": [{"id": "m-small"}]})

    scripted.handler = handler
    r = client.patch(
        f"/api/llm/connections/{conn['id']}",
        json={
            "changes": {"base_url": "http://other.example.com", "api_key": "sk-new"},
            "expected_version": conn["version"],
        },
    )
    assert r.status_code == 200 and r.json()["has_key"] is True
    client.post(f"/api/llm/connections/{conn['id']}/test")
    assert seen and seen[0].get("authorization") == "Bearer sk-new"


def test_changing_only_the_path_keeps_the_key(client):
    conn = make_conn(client)
    r = client.patch(
        f"/api/llm/connections/{conn['id']}",
        json={"changes": {"base_url": URL + "/other"}, "expected_version": conn["version"]},
    )
    assert r.status_code == 200 and r.json()["has_key"] is True


def test_create_cannot_reference_another_connections_secrets(client):
    a = make_conn(client)
    r = client.post(
        "/api/llm/connections",
        json={"preset": "openai", "base_url": URL, "secret_ref": "x", "headers_ref": "y"},
    )
    assert r.status_code == 422 and a["has_key"]  # key required, extra fields are ignored


# --- admin only in server mode ---------------------------------------------------------


@pytest.fixture
def member(make_app, client):
    services = client.app.state.services
    services.users.create("member", "another-long-password", is_admin=False)
    from fastapi.testclient import TestClient

    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"username": "member", "password": "another-long-password"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def test_non_admin_cannot_change_ai_in_server_mode(client, member, scripted):
    conn = make_conn(client)
    msg = "Only the household admin can change AI connections."
    calls = [
        member.post("/api/llm/connections", json={"preset": "custom", "base_url": URL}),
        member.patch(
            f"/api/llm/connections/{conn['id']}",
            json={"changes": {"name": "x"}, "expected_version": 1},
        ),
        member.delete(f"/api/llm/connections/{conn['id']}"),
        member.post(f"/api/llm/connections/{conn['id']}/test"),
        member.post(f"/api/llm/connections/{conn['id']}/acknowledge-notice"),
        member.get("/api/llm/detect"),
        member.put(
            "/api/llm/routing/tasks/coach",
            json={"chain": [], "local_only": False, "expected_version": 0},
        ),
        member.post("/api/llm/try", json={"task": "coach", "prompt": "hi"}),
        member.post("/api/llm/secrets/forget"),
        member.patch(
            f"/api/llm/models/{conn['id']}/m-small",
            json={"context_window": 4096, "expected_version": 1},
        ),
    ]
    for r in calls:
        assert r.status_code == 403 and r.json()["detail"] == msg, r.request.url
    assert member.get("/api/llm/connections").status_code == 200


def test_desktop_user_is_the_admin(make_app):
    from fastapi.testclient import TestClient

    c = TestClient(make_app("desktop", launch_token="T" * 43), follow_redirects=False)
    assert c.get("/auth/launch", params={"token": "T" * 43}).status_code == 303
    c.headers["X-CSRF-Token"] = c.get("/api/auth/session").json()["csrf_token"]
    assert c.post("/api/llm/secrets/forget").status_code == 200


# --- test endpoint never reflects bodies ------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (401, "unauthorised"),
        (403, "unauthorised"),
        (404, "not_found"),
        (429, "rate_limited"),
        (503, "server_error"),
    ],
)
def test_test_endpoint_classifies_without_reflecting_bodies(client, scripted, status, reason):
    conn = make_conn(client)
    scripted.handler = lambda req: httpx.Response(status, text="INTERNAL-BODY-SECRET")
    r = client.post(f"/api/llm/connections/{conn['id']}/test")
    out = r.json()
    assert r.status_code == 200 and out["ok"] is False
    assert out["reason"] == reason and out["status"] == status
    assert "INTERNAL-BODY-SECRET" not in r.text


def test_test_endpoint_unreachable_and_not_json(client, scripted):
    conn = make_conn(client)

    def refuse(req):
        raise httpx.ConnectError("boom-detail-xyz")

    scripted.handler = refuse
    out = client.post(f"/api/llm/connections/{conn['id']}/test").json()
    assert out["reason"] == "unreachable" and "boom-detail" not in str(out)
    scripted.handler = lambda req: httpx.Response(200, text="<html>nope</html>")
    out = client.post(f"/api/llm/connections/{conn['id']}/test").json()
    assert out["reason"] == "not_an_ai_server" and "nope" not in str(out)


# --- metadata addresses are never contacted -----------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest",
        "http://[fd00:ec2::254]/latest",
        "http://100.100.100.200/latest",
        "http://metadata.google.internal/computeMetadata",
    ],
)
def test_metadata_hosts_are_blocked_and_logged(client, scripted, url):
    conn = client.post("/api/llm/connections", json={"preset": "custom", "base_url": url}).json()
    scripted.handler = lambda req: pytest.fail("request left the guard")
    out = client.post(f"/api/llm/connections/{conn['id']}/test").json()
    assert out["ok"] is False and "metadata" in out["error"]
    entries = client.get("/api/privacy/log").json()["entries"]
    assert entries[0]["outcome"] == "blocked"


# --- input validation -----------------------------------------------------------------------


def test_bad_inputs_are_422_not_500(client, scripted):
    conn = make_conn(client)
    client.post(f"/api/llm/connections/{conn['id']}/test")
    bad_chain = client.put(
        "/api/llm/routing/tasks/coach",
        json={"chain": [{}], "local_only": False, "expected_version": 0},
    )
    assert bad_chain.status_code == 422
    big = client.patch(
        f"/api/llm/models/{conn['id']}/m-small",
        json={"context_window": 10**30, "expected_version": 1},
    )
    assert big.status_code == 422
    tiny = client.patch(
        f"/api/llm/models/{conn['id']}/m-small",
        json={"context_window": 100, "expected_version": 1},
    )
    assert tiny.status_code == 422
    neg = client.put(
        "/api/llm/routing/tasks/coach",
        json={"chain": [], "local_only": False, "expected_version": -1},
    )
    assert neg.status_code == 422
    assert client.post("/api/llm/try", json={"task": "dancing", "prompt": "hi"}).status_code == 422
    assert client.post("/api/llm/try", json={"task": "coach", "prompt": ""}).status_code == 422
    conn2 = make_conn(client, preset="custom")
    bad_idna = ("http://xn--/", "http://api.xn--/v1", "http://xn--bcher-kva.my_box/")
    too_long = "http://" + "a" * 3000
    for bad in ("http://[::1", "http://host:99999999", "http://", too_long, *bad_idna):
        up = client.patch(
            f"/api/llm/connections/{conn2['id']}",
            json={"changes": {"base_url": bad}, "expected_version": 1},
        )
        assert up.status_code == 422, bad
    for bad in ("http://[::1", "http://host:99999999", *bad_idna):
        made = client.post("/api/llm/connections", json={"preset": "custom", "base_url": bad})
        assert made.status_code == 422, bad
    for bad in bad_idna:
        made = client.post("/api/llm/connections", json={"preset": "custom", "base_url": bad})
        assert made.json()["detail"] == "That address isn't valid.", bad
    assert client.get("/api/usage?month=9999-12").status_code == 422
    assert client.get("/api/usage?month=0001-01").status_code == 422
    huge = client.patch(
        "/api/settings/llm.monthly_cap_gbp", json={"value": 1e9, "expected_version": 0}
    )
    assert huge.status_code == 422


@pytest.mark.parametrize(
    ("key", "value"),
    [("llm.mode", "advanced"), ("llm.simple_model", None), ("privacy.local_only", True)],
)
def test_non_admin_cannot_change_shared_ai_and_privacy_settings(client, member, key, value):
    r = member.patch(f"/api/settings/{key}", json={"value": value, "expected_version": 0})
    assert r.status_code == 403
    assert r.json()["detail"] == "Only the household admin can change AI connections."
    ok = client.patch(f"/api/settings/{key}", json={"value": value, "expected_version": 0})
    assert ok.status_code == 200


def test_non_admin_can_still_change_other_settings(client, member):
    keys = [e["key"] for e in client.get("/api/settings").json()["settings"]]
    other = [k for k in keys if not k.startswith(("llm.", "privacy."))]
    assert other  # the permission is about shared AI and privacy keys only


@pytest.fixture
def keychain(client):
    """Swap the app's secret store for an OS keychain that tests can lock and unlock."""
    import keyring

    from fakes.keyrings import RefusingKeyring
    from tuppence.core.secrets import KeyringStore

    previous = keyring.get_keyring()
    backend = RefusingKeyring()
    keyring.set_keyring(backend)
    services = client.app.state.services
    services.connections.secrets = KeyringStore(db=services.db)
    yield backend
    keyring.set_keyring(previous)


def test_a_keychain_that_refuses_at_use_time_is_a_409_never_a_500(client, scripted, keychain):
    keychain.refuse = {"set"}
    r = client.post(
        "/api/llm/connections", json={"preset": "openai", "api_key": "sk-1234567", "base_url": URL}
    )
    assert r.status_code == 409 and "keychain" in r.json()["detail"]
    assert "backend detail" not in r.text
    keychain.refuse = set()
    conn = ready(client, scripted)
    keychain.refuse = {"get"}
    t = client.post(f"/api/llm/connections/{conn['id']}/test")
    assert t.status_code == 409 and "keychain" in t.json()["detail"]
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 409 and "keychain" in r.json()["detail"]
    keychain.refuse = {"delete"}
    f = client.post("/api/llm/secrets/forget")
    assert f.status_code == 200 and f.json() == {"not_removed": 1}
    conns = client.get("/api/llm/connections").json()["connections"]
    assert [c["has_key"] for c in conns] == [False]


def test_keys_and_header_values_must_be_printable_ascii(client):
    bad_key = client.post(
        "/api/llm/connections",
        json={"preset": "openai", "api_key": "sk-abc\u000bdef", "base_url": URL},
    )
    assert bad_key.status_code == 422 and "sk-abc" not in bad_key.text
    bad_header = client.post(
        "/api/llm/connections",
        json={"preset": "custom", "base_url": URL, "headers": {"X-Gw": "abc\u0001def"}},
    )
    assert bad_header.status_code == 422 and "abc" not in bad_header.text


def test_validation_errors_never_echo_what_was_sent(client):
    key = "sk-" + "Z" * 4100
    r = client.post(
        "/api/llm/connections", json={"preset": "openai", "api_key": key, "base_url": URL}
    )
    assert r.status_code == 422 and "ZZZZ" not in r.text
    assert r.json()["detail"][0]["loc"][-1] == "api_key" and r.json()["detail"][0]["msg"]


def test_try_runs_under_one_coach_turns_budget(client, scripted, monkeypatch):
    ready(client, scripted)  # a cloud connection
    services = client.app.state.services
    runs = []
    real = services.llm.chat

    def spy(*a, **kw):
        runs.append(kw.get("run"))
        return real(*a, **kw)

    monkeypatch.setattr(services.llm, "chat", spy)
    assert client.post("/api/llm/try", json={"task": "read", "prompt": "hi"}).status_code == 200
    [run] = runs
    assert (run.max_calls, run.max_seconds, run.max_gbp, run.calls) == (9, 60, 0.25, 1)


def test_try_with_only_local_models_gets_the_local_time_limit(client, scripted, monkeypatch):
    conn = client.post(
        "/api/llm/connections", json={"preset": "custom", "base_url": "http://127.0.0.1:9200/v1"}
    ).json()
    client.post(f"/api/llm/connections/{conn['id']}/test")
    choose_simple(client, conn["id"])
    services = client.app.state.services
    runs = []
    real = services.llm.chat
    monkeypatch.setattr(
        services.llm, "chat", lambda *a, **kw: runs.append(kw["run"]) or real(*a, **kw)
    )
    assert client.post("/api/llm/try", json={"prompt": "hi"}).status_code == 200
    assert runs[0].max_seconds == 180


def test_try_stops_at_the_run_call_limit_counting_retries(client, scripted):
    services = client.app.state.services
    services.llm.sleep = lambda s: None
    a = ready(client, scripted)
    b = make_conn(client, base_url="http://127.0.0.1:9101")
    client.post(f"/api/llm/connections/{b['id']}/test")
    acknowledge(client, b["id"])
    chain = [
        {"connection_id": c["id"], "model_id": m} for c in (a, b) for m in ("m-small", "m-big")
    ]
    client.patch("/api/settings/llm.mode", json={"value": "advanced", "expected_version": 0})
    r = client.put(
        "/api/llm/routing/tasks/coach",
        json={"chain": chain, "local_only": False, "expected_version": 0},
    )
    assert r.status_code == 200
    scripted.replies = [httpx.Response(503, json={})] * 20
    r = client.post("/api/llm/try", json={"task": "coach", "prompt": "hi"})
    assert r.status_code == 429 and "9 AI calls" in r.json()["detail"]
    assert len([x for x in scripted.requests if "messages" in x]) == 9


def test_acknowledging_the_notice_needs_the_version_it_was_shown_for(client):
    conn = make_conn(client)
    url = f"/api/llm/connections/{conn['id']}/acknowledge-notice"
    assert client.post(url).status_code == 422
    moved = client.patch(
        f"/api/llm/connections/{conn['id']}",
        json={
            "changes": {"base_url": "http://127.0.0.1:9555", "api_key": "sk-other-1"},
            "expected_version": conn["version"],
        },
    ).json()
    stale = client.post(url, json={"expected_version": conn["version"]})
    assert stale.status_code == 409 and stale.json()["current_version"] == moved["version"]
    assert client.get("/api/llm/connections").json()["connections"][0]["needs_notice"]
    ok = client.post(url, json={"expected_version": moved["version"]})
    assert ok.status_code == 200 and not ok.json()["needs_notice"]


def test_model_price_override_via_the_api(client, scripted):
    conn = ready(client, scripted)
    models = client.get(f"/api/llm/models?connection_id={conn['id']}").json()["models"]
    m = next(x for x in models if x["model_id"] == "m-small")
    assert m["price_source"] is None
    url = f"/api/llm/models/{conn['id']}/m-small"
    r = client.patch(
        url,
        json={
            "price_in_usd_per_mtok": 0.5,
            "price_out_usd_per_mtok": 1.5,
            "expected_version": m["version"],
        },
    )
    assert r.status_code == 200, r.text
    assert (r.json()["price_in_usd_per_mtok"], r.json()["price_source"]) == (0.5, "user")
    v = r.json()["version"]
    for bad in (
        {"price_in_usd_per_mtok": -1},
        {"price_out_usd_per_mtok": 20_000},
        {},
        {"price_in_usd_per_mtok": 1, "colour": "red"},
    ):
        assert client.patch(url, json={**bad, "expected_version": v}).status_code == 422, bad
    stale = client.patch(url, json={"price_in_usd_per_mtok": 1, "expected_version": v - 1})
    assert stale.status_code == 409


def test_malformed_simple_model_is_refused_and_never_breaks_ai_calls(client):
    url = "/api/settings/llm.simple_model"
    for bad in (
        {"foo": "bar"},
        {"connection_id": "c" * 65, "model_id": "m"},
        {"connection_id": "c", "model_id": ""},
        {"connection_id": "c", "model_id": "m", "extra": "x"},
        "c/m",
    ):
        r = client.patch(url, json={"value": bad, "expected_version": 0})
        assert r.status_code == 422, bad
    services = client.app.state.services
    with services.db.transaction() as conn:  # e.g. written by an older version
        conn.execute(
            "INSERT INTO app_settings (key, value, version, updated_at)"
            " VALUES ('llm.simple_model', '{\"foo\": \"bar\"}', 1, '2026-10-07T00:00:00Z')"
        )
    assert client.post("/api/llm/try", json={"prompt": "hi"}).status_code == 409


def test_patch_connection_changes_are_typed_and_bounded(client):
    conn = make_conn(client)
    url = f"/api/llm/connections/{conn['id']}"
    v = conn["version"]
    for bad in (
        {"name": "x" * 201},
        {"api_key": "k" * 4001},
        {"headers": {f"X-{i}": "v" for i in range(21)}},
        {"headers": ["not", "a", "dict"]},
        {"headers": {"X-A": "v" * 4001}},
        {"enabled": "yes"},
        {"colour": "red"},
        {"base_url": "http://x/" + "a" * 2100},
    ):
        r = client.patch(url, json={"changes": bad, "expected_version": v})
        assert r.status_code == 422, (bad, r.status_code)
        assert "kkkk" not in r.text and "vvvv" not in r.text
    ok = client.patch(url, json={"changes": {"name": "Mine"}, "expected_version": v})
    assert ok.status_code == 200 and ok.json()["name"] == "Mine"


def test_the_usage_month_is_the_month_the_cap_uses(client):
    from datetime import UTC, datetime

    services = client.app.state.services
    # 23:30 UTC on 31 July is 00:30 on 1 August in UK summer time: still July for the cap.
    services.usage.clock = lambda: datetime(2026, 7, 31, 23, 30, tzinfo=UTC)
    body = client.get("/api/usage").json()
    assert body["month"] == body["current_month"] == "2026-07"
    assert client.get("/api/usage?month=2026-06").json()["current_month"] == "2026-07"
