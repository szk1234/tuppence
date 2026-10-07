def test_state_and_steps(client):
    s = client.get("/api/onboarding").json()
    assert s["next_step"] == "welcome" and s["started"] is False
    assert s["steps"][0] == {"id": "welcome", "title": "Welcome", "status": "todo"}
    assert any(p["id"] == "nation" for p in s["prompts"])
    r = client.post("/api/onboarding/steps/welcome", json={"status": "done"})
    assert r.status_code == 200 and r.json()["started"] is True
    assert r.json()["next_step"] == "household"
    assert (
        client.post("/api/onboarding/steps/welcome", json={"status": "skipped"}).status_code == 200
    )
    assert client.post("/api/onboarding/steps/nope", json={"status": "done"}).status_code == 422
    assert client.post("/api/onboarding/steps/welcome", json={"status": "todo"}).status_code == 422


def test_reset(client):
    client.post("/api/onboarding/steps/welcome", json={"status": "done"})
    r = client.post("/api/onboarding/reset")
    assert r.status_code == 200 and r.json()["started"] is False


def test_needs_session_and_csrf(anon_client, client):
    assert anon_client.get("/api/onboarding").status_code == 401
    assert anon_client.post("/api/onboarding/reset").status_code == 401
    bad = client.post("/api/onboarding/reset", headers={"X-CSRF-Token": "wrong"})
    assert bad.status_code == 403
