def test_agents_api(client):
    r = client.get("/api/config/agents")
    assert r.status_code == 200
    body = r.json()
    assert body["preset"] == {"value": "balanced", "version": 0}
    names = [a["manifest"]["name"] for a in body["agents"]]
    assert "researcher" in names

    upd = client.patch(
        "/api/config/agents/researcher",
        json={"changes": {"limits": {"max_merchants_per_run": 10}}, "expected_version": 0},
    )
    assert (
        upd.status_code == 200 and upd.json()["manifest"]["limits"]["max_merchants_per_run"] == 10
    )

    bad = client.patch(
        "/api/config/agents/researcher",
        json={"changes": {"budgets": {"max_gbp": -1}}, "expected_version": 1},
    )
    assert bad.status_code == 422

    reset = client.post(
        "/api/config/agents/researcher/reset",
        json={"path": "limits.max_merchants_per_run", "expected_version": 1},
    )
    assert (
        reset.status_code == 200
        and reset.json()["manifest"]["limits"]["max_merchants_per_run"] == 20
    )

    assert client.get("/api/config/presets").json() == {
        "presets": ["balanced", "frugal", "thorough"]
    }
    exp = client.get("/api/config/export")
    assert exp.status_code == 200 and "attachment" in exp.headers["content-disposition"]


def test_unknown_agent_404(client):
    assert client.get("/api/config/agents/nope").status_code == 404


def test_raw_infinity_override_rejected_and_export_still_works(client):
    for literal in ("Infinity", "NaN", "-Infinity"):
        r = client.patch(
            "/api/config/agents/linter",
            content='{"changes": {"budgets": {"max_gbp": LIT}}, "expected_version": 0}'.replace(
                "LIT", literal
            ),
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 422, literal
    r = client.patch(
        "/api/config/agents/backlog_sweep",
        content='{"changes": {"thresholds": {"revisit_below_confidence": NaN}},'
        ' "expected_version": 0}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    assert client.get("/api/config/export").status_code == 200
    assert client.get("/api/config/agents/linter").json()["version"] == 0
