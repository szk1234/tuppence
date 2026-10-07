def _person(client):
    return client.post(
        "/api/household/people", json={"display_name": "Alex Example", "role": "adult"}
    ).json()["id"]


def test_debts_api_roundtrip(client):
    pid = _person(client)
    r = client.post(
        "/api/debts",
        json={
            "kind": "car_finance_pcp",
            "lender": "Motor Co",
            "person_id": pid,
            "balance": "£8,000",
            "apr": 9.9,
            "details": {"agreement_start": "2019-03-01", "via_broker": True, "balloon": "5000"},
        },
    )
    assert r.status_code == 201
    d = r.json()
    assert d["balance"] == "8000.00" and d["balance_date"]
    assert d["car_finance_redress_window"] is True and d["details"]["balloon"] == "5000.00"

    unsure = client.post(
        "/api/debts", json={"kind": "student_loan", "lender": "SLC", "balance": "1"}
    )
    assert unsure.status_code == 201 and unsure.json()["student_loan_plan"] is None
    high = client.post(
        "/api/debts", json={"kind": "other", "lender": "Lender", "balance": "1", "apr": 1200}
    )
    assert high.status_code == 422
    assert (
        high.json()["detail"] == "Enter an APR between 0 and 1000, with at most 2 decimal places."
    )
    assert (
        client.post(
            "/api/debts", json={"kind": "other", "lender": "Lender", "balance": "1", "apr": 400}
        ).status_code
        == 201
    )
    unk = client.post(
        "/api/debts",
        json={"kind": "other", "lender": "X", "balance": "1", "details": {"zzz": 1}},
    )
    assert unk.status_code == 422 and "zzz" in unk.json()["detail"]
    assert (
        client.post(
            "/api/debts", json={"kind": "informal", "lender": "Bro", "balance": "1"}
        ).status_code
        == 422
    )

    up = client.patch(
        f"/api/debts/{d['id']}", json={"changes": {"balance": "7500"}, "expected_version": 1}
    )
    assert up.status_code == 200 and up.json()["balance"] == "7500.00"
    stale = client.patch(
        f"/api/debts/{d['id']}", json={"changes": {"lender": "X"}, "expected_version": 1}
    )
    assert stale.status_code == 409 and stale.json()["current_version"] == 2
    assert len(client.get("/api/debts").json()["debts"]) == 3
    s = client.post(f"/api/debts/{d['id']}/settle", json={"expected_version": 2})
    assert s.status_code == 200 and s.json()["status"] == "settled"
    assert d["id"] not in [x["id"] for x in client.get("/api/debts").json()["debts"]]
    settled = client.get("/api/debts", params={"include_settled": True}).json()["debts"]
    assert [x["status"] for x in settled if x["id"] == d["id"]] == ["settled"]


def test_goals_api_roundtrip(client):
    assert client.get("/api/goals/suggestions").json() == {"emergency_fund": True}
    r = client.post(
        "/api/goals",
        json={"name": "Rainy day", "kind": "emergency_fund", "target_amount": "3000"},
    )
    assert r.status_code == 201
    g = r.json()
    assert g["target_amount"] == "3000.00" and g["saved_amount"] == "0.00"
    assert client.get("/api/goals/suggestions").json() == {"emergency_fund": False}
    past = client.post(
        "/api/goals", json={"name": "X", "kind": "other", "target_date": "2020-01-01"}
    )
    assert past.status_code == 422
    up = client.patch(
        f"/api/goals/{g['id']}", json={"changes": {"saved_amount": "100"}, "expected_version": 1}
    )
    assert up.json()["saved_amount"] == "100.00"
    stale = client.patch(
        f"/api/goals/{g['id']}", json={"changes": {"name": "Y"}, "expected_version": 1}
    )
    assert stale.status_code == 409
    done = client.post(
        f"/api/goals/{g['id']}/status", json={"status": "achieved", "expected_version": 2}
    )
    assert done.status_code == 200 and done.json()["status"] == "achieved"
    assert client.get("/api/goals").json()["goals"] == []
