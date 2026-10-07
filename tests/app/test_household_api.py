def test_people_api_roundtrip(client):
    r = client.post("/api/household/people", json={"display_name": "Alex Example", "role": "adult"})
    assert r.status_code == 201
    pid = r.json()["id"]
    people = client.get("/api/household/people").json()["people"]
    assert [p["id"] for p in people] == [pid]
    upd = client.patch(
        f"/api/household/people/{pid}",
        json={"changes": {"display_name": "Alex E."}, "expected_version": 1},
    )
    assert upd.status_code == 200 and upd.json()["version"] == 2
    stale = client.patch(
        f"/api/household/people/{pid}",
        json={"changes": {"display_name": "X"}, "expected_version": 1},
    )
    assert stale.status_code == 409
    gone = client.post(f"/api/household/people/{pid}/retire", json={"expected_version": 2})
    assert gone.status_code == 200 and gone.json()["status"] == "retired"


def test_household_patch_rejects_full_postcode(client):
    h = client.get("/api/household").json()
    r = client.patch(
        "/api/household",
        json={"changes": {"postcode_district": "LS6 2AB"}, "expected_version": h["version"]},
    )
    assert r.status_code == 422 and "first part of your postcode" in r.json()["detail"]
    assert client.get("/api/household").json()["postcode_district"] is None


def test_timeline_api(client):
    pid = client.post(
        "/api/household/people", json={"display_name": "Alex Example", "role": "adult"}
    ).json()["id"]
    r = client.post(
        "/api/household/timeline",
        json={
            "subject_type": "person",
            "subject_id": pid,
            "attribute": "employment_status",
            "value": "employed",
            "valid_from": "2024-01-01",
        },
    )
    assert r.status_code == 201
    entries = client.get(
        "/api/household/timeline", params={"subject_type": "person", "subject_id": pid}
    ).json()["entries"]
    assert entries[0]["value"] == "employed" and entries[0]["valid_to"] is None


def test_household_requires_sign_in(anon_client):
    assert anon_client.get("/api/household").status_code == 401
