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


def test_timeline_api_full_postcode_rejected_and_district_uppercased(client):
    base = {"subject_type": "household", "subject_id": "1", "attribute": "postcode_district"}
    bad = client.post(
        "/api/household/timeline", json={**base, "value": "LS6 2AB", "valid_from": "2026-01-01"}
    )
    assert bad.status_code == 422 and "first part of your postcode" in bad.json()["detail"]
    q = {"subject_type": "household", "subject_id": "1"}
    assert client.get("/api/household/timeline", params=q).json()["entries"] == []
    ok = client.post(
        "/api/household/timeline", json={**base, "value": "ls6", "valid_from": "2026-01-01"}
    )
    assert ok.status_code == 201 and ok.json()["value"] == "LS6"


def test_timeline_unknown_subject_404(client):
    r = client.post(
        "/api/household/timeline",
        json={
            "subject_type": "person",
            "subject_id": "p_nobody",
            "attribute": "household_member",
            "value": True,
            "valid_from": "2026-01-01",
        },
    )
    assert r.status_code == 404


def test_null_required_field_422_and_stale_empty_patch_409(client):
    pid = client.post(
        "/api/household/people", json={"display_name": "Alex", "role": "adult"}
    ).json()["id"]
    r = client.patch(
        f"/api/household/people/{pid}",
        json={"changes": {"display_name": None}, "expected_version": 1},
    )
    assert r.status_code == 422
    client.patch(
        f"/api/household/people/{pid}", json={"changes": {"role": "child"}, "expected_version": 1}
    )
    stale = client.patch(
        f"/api/household/people/{pid}", json={"changes": {}, "expected_version": 1}
    )
    assert stale.status_code == 409


def _nation_entries(client):
    q = {"subject_type": "household", "subject_id": "1"}
    entries = client.get("/api/household/timeline", params=q).json()["entries"]
    return [
        (e["value"], e["valid_from"], e["valid_to"]) for e in entries if e["attribute"] == "nation"
    ]


def test_household_patch_and_timeline_agree(client):
    from datetime import date

    today = date.today().isoformat()
    h = client.get("/api/household").json()
    r = client.patch(
        "/api/household", json={"changes": {"nation": "wales"}, "expected_version": h["version"]}
    )
    assert r.status_code == 200
    assert _nation_entries(client) == [("wales", today, None)]
    base = {"subject_type": "household", "subject_id": "1", "attribute": "nation"}
    past = client.post(
        "/api/household/timeline", json={**base, "value": "scotland", "valid_from": "2020-01-01"}
    )
    assert past.status_code == 201
    assert client.get("/api/household").json()["nation"] == "wales"  # today's entry wins
    assert _nation_entries(client)[-1] == ("wales", today, None)


def test_past_timeline_entry_shows_on_the_household(client):
    base = {"subject_type": "household", "subject_id": "1", "attribute": "nation"}
    r = client.post(
        "/api/household/timeline", json={**base, "value": "scotland", "valid_from": "2020-01-01"}
    )
    assert r.status_code == 201
    assert client.get("/api/household").json()["nation"] == "scotland"
    future = client.post(
        "/api/household/timeline", json={**base, "value": "england", "valid_from": "2099-01-01"}
    )
    assert future.status_code == 201
    assert client.get("/api/household").json()["nation"] == "scotland"


def test_timeline_expected_current_conflict_and_versioned_delete(client):
    base = {"subject_type": "household", "subject_id": "1", "attribute": "bedrooms"}
    first = client.post(
        "/api/household/timeline",
        json={**base, "value": 2, "valid_from": "2024-01-01", "expected_current": None},
    )
    assert first.status_code == 201
    stale = client.post(
        "/api/household/timeline",
        json={**base, "value": 3, "valid_from": "2025-01-01", "expected_current": None},
    )
    assert stale.status_code == 409 and "current_version" in stale.json()
    ok = client.post(
        "/api/household/timeline",
        json={**base, "value": 3, "valid_from": "2025-01-01", "expected_current": 2},
    )
    assert ok.status_code == 201
    entry = ok.json()
    wrong = client.delete(
        f"/api/household/timeline/{entry['id']}", params={"expected_version": entry["version"] + 1}
    )
    assert wrong.status_code == 409
    gone = client.delete(
        f"/api/household/timeline/{entry['id']}", params={"expected_version": entry["version"]}
    )
    assert gone.status_code == 204
    entries = client.get("/api/household/timeline", params=base | {}).json()["entries"]
    assert [e["value"] for e in entries] == [2] and entries[0]["valid_to"] is None


def test_deleting_only_district_entry_nulls_household(client):
    base = {"subject_type": "household", "subject_id": "1", "attribute": "postcode_district"}
    e = client.post(
        "/api/household/timeline", json={**base, "value": "LS6", "valid_from": "2020-01-01"}
    ).json()
    assert client.get("/api/household").json()["postcode_district"] == "LS6"
    r = client.delete(
        f"/api/household/timeline/{e['id']}", params={"expected_version": e["version"]}
    )
    assert r.status_code == 204
    assert client.get("/api/household").json()["postcode_district"] is None


def test_money_timeline_attribute_takes_pounds_and_overflow_is_422(client):
    base = {
        "subject_type": "household",
        "subject_id": "1",
        "attribute": "housing_monthly_pence",
        "valid_from": "2025-01-01",
    }
    ok = client.post("/api/household/timeline", json={**base, "value": "1450.00"})
    assert ok.status_code == 201 and ok.json()["value"] == 145000
    for bad in ["99999999999999999999999.99", 1450, 1e300]:
        assert (
            client.post("/api/household/timeline", json={**base, "value": bad}).status_code == 422
        )
