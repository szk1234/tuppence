def _person(client, name="Alex Example"):
    return client.post(
        "/api/household/people", json={"display_name": name, "role": "adult"}
    ).json()["id"]


RULE = {"type": "monthly_day", "day": 25}


def test_income_api_roundtrip(client):
    a, b = _person(client), _person(client, "Sam Example")
    r = client.post(
        "/api/income",
        json={
            "person_id": a,
            "kind": "salary",
            "name": "Salary",
            "net_amount": "£2,345.67",
            "pay_rule": RULE,
            "variable_components": ["bonus"],
        },
    )
    assert r.status_code == 201
    inc = r.json()
    assert inc["net_amount"] == "2345.67" and inc["account_id"] is None
    assert inc["next_pay_date"] and inc["pay_rule_description"]

    acct = client.post(
        "/api/accounts",
        json={"provider": "monzo", "kind": "current", "nickname": "J", "owner_ids": [a, b]},
    ).json()
    linked = client.patch(
        f"/api/income/{inc['id']}",
        json={"changes": {"account_id": acct["id"]}, "expected_version": 1},
    )
    assert linked.status_code == 200 and linked.json()["account_id"] == acct["id"]
    stale = client.patch(
        f"/api/income/{inc['id']}", json={"changes": {"name": "X"}, "expected_version": 1}
    )
    assert stale.status_code == 409 and stale.json()["current_version"] == 2

    up = client.get("/api/income/upcoming", params={"days": 70}).json()["upcoming"]
    assert up and set(up[0]) == {"income_id", "label", "date", "amount"}
    assert up[0]["amount"] == "2345.67" and up[0]["label"] == "Salary"

    prev = client.post("/api/income/preview-rule", json={"pay_rule": RULE})
    assert prev.status_code == 200 and len(prev.json()["next_dates"]) == 5
    assert prev.json()["description"]
    assert len(client.get("/api/income").json()["income"]) == 1

    ended = client.post(f"/api/income/{inc['id']}/end", json={"expected_version": 2})
    assert ended.status_code == 200 and ended.json()["status"] == "ended"
    assert client.get("/api/income").json()["income"] == []


def test_income_validation_is_422(client):
    a = _person(client)
    base = {"person_id": a, "kind": "salary", "name": "Pay", "net_amount": "100"}
    bad_rule = client.post("/api/income", json={**base, "pay_rule": {"type": "x"}})
    assert bad_rule.status_code == 422
    bad_rule = client.post("/api/income/preview-rule", json={"pay_rule": {"type": "x"}})
    assert bad_rule.status_code == 422
    bad_money = client.post("/api/income", json={**base, "net_amount": "abc", "pay_rule": RULE})
    assert bad_money.status_code == 422
    bad_kind = client.post("/api/income", json={**base, "kind": "lottery", "pay_rule": RULE})
    assert bad_kind.status_code == 422


def test_calendar_assumed_in_responses(client):
    a = _person(client)
    client.post(
        "/api/income",
        json={"person_id": a, "kind": "salary", "name": "Pay", "net_amount": "1", "pay_rule": RULE},
    )
    assert client.get("/api/income").json()["income"][0]["calendar_assumed"] is True
    assert client.get("/api/income/upcoming").json()["calendar_assumed"] is True
    assert (
        client.post("/api/income/preview-rule", json={"pay_rule": RULE}).json()["calendar_assumed"]
        is True
    )
    bad = client.post(
        "/api/income",
        json={"person_id": a, "kind": "salary", "name": "P", "net_amount": 5, "pay_rule": RULE},
    )
    assert bad.status_code == 422 and "pounds" in bad.json()["detail"]
