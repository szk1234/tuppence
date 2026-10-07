def test_list_settings(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    keys = {s["key"] for s in r.json()["settings"]}
    assert "privacy.local_only" in keys


def test_patch_setting_and_conflict(client):
    r = client.patch(
        "/api/settings/privacy.local_only", json={"value": True, "expected_version": 0}
    )
    assert r.status_code == 200 and r.json()["version"] == 1
    stale = client.patch(
        "/api/settings/privacy.local_only", json={"value": False, "expected_version": 0}
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "detail": "This was changed somewhere else. Reload and try again.",
        "current_version": 1,
    }


def test_patch_invalid_value_is_422(client):
    r = client.patch("/api/settings/config.preset", json={"value": "turbo", "expected_version": 0})
    assert r.status_code == 422 and "config.preset" in r.json()["detail"]


def test_patch_unknown_key_is_404(client):
    assert (
        client.patch("/api/settings/nope", json={"value": 1, "expected_version": 0}).status_code
        == 404
    )


def test_database_created_in_data_dir(tmp_path, make_app):
    make_app()
    assert (tmp_path / "data" / "tuppence.db").exists()


def test_raw_infinity_is_never_stored(client):
    r = client.patch(
        "/api/settings/llm.monthly_cap_gbp",
        content='{"value": Infinity, "expected_version": 0}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code in (400, 422)
    listing = client.get("/api/settings")
    assert listing.status_code == 200
    cap = next(s for s in listing.json()["settings"] if s["key"] == "llm.monthly_cap_gbp")
    assert cap["version"] == 0 and cap["value"] == 10.0


def test_bad_stored_value_get_ok_and_patch_fixes(client):
    services = client.app.state.services
    with services.db.transaction() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value, version, updated_at) "
            "VALUES ('llm.monthly_cap_gbp', 'Infinity', 4, 'x')"
        )
    r = client.get("/api/settings")
    assert r.status_code == 200
    cap = next(s for s in r.json()["settings"] if s["key"] == "llm.monthly_cap_gbp")
    assert cap["value"] == 10.0 and cap["version"] == 4
    fixed = client.patch(
        "/api/settings/llm.monthly_cap_gbp", json={"value": 5, "expected_version": 4}
    )
    assert fixed.status_code == 200 and fixed.json()["value"] == 5.0
