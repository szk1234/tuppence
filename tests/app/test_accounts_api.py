def _person(client, name="Alex Example"):
    r = client.post("/api/household/people", json={"display_name": name, "role": "adult"})
    return r.json()["id"]


def test_accounts_api_roundtrip(client):
    a, b = _person(client), _person(client, "Sam Example")
    r = client.post(
        "/api/accounts",
        json={"provider": "monzo", "kind": "current", "nickname": "Joint", "owner_ids": [a, b]},
    )
    assert r.status_code == 201 and r.json()["joint"] is True
    acct = r.json()
    card = client.post(
        "/api/accounts",
        json={
            "provider": "barclaycard",
            "kind": "credit_card",
            "nickname": "Barclaycard",
            "owner_ids": [a],
            "credit_limit": "£2,500",
            "promo_end": "2027-03-31",
        },
    )
    assert card.status_code == 201 and card.json()["credit_limit"] == "2500.00"
    assert len(client.get("/api/accounts").json()["accounts"]) == 2

    upd = client.patch(
        f"/api/accounts/{acct['id']}", json={"changes": {"owner_ids": [a]}, "expected_version": 1}
    )
    assert upd.status_code == 200 and upd.json()["version"] == 2 and not upd.json()["joint"]
    stale = client.patch(
        f"/api/accounts/{acct['id']}", json={"changes": {"nickname": "X"}, "expected_version": 1}
    )
    assert stale.status_code == 409 and stale.json()["current_version"] == 2

    closed = client.post(f"/api/accounts/{acct['id']}/close", json={"expected_version": 2})
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert len(client.get("/api/accounts").json()["accounts"]) == 1
    everything = client.get("/api/accounts", params={"include_closed": "true"}).json()["accounts"]
    assert len(everything) == 2
    back = client.post(f"/api/accounts/{acct['id']}/reopen", json={"expected_version": 3})
    assert back.status_code == 200 and back.json()["status"] == "active"
    assert (
        client.post(f"/api/accounts/{acct['id']}/reopen", json={"expected_version": 3}).status_code
        == 409
    )


def test_accounts_api_validation(client):
    a = _person(client)
    base = {"provider": "monzo", "kind": "current", "nickname": "X", "owner_ids": [a]}
    assert client.post("/api/accounts", json={**base, "last4": "12a4"}).status_code == 422
    assert client.post("/api/accounts", json={**base, "owner_ids": ["p_nobody"]}).status_code == 422
    assert client.post("/api/accounts", json={**base, "credit_limit": "100"}).status_code == 422
    assert client.post("/api/accounts", json={**base, "provider": "other"}).status_code == 422
    assert client.post("/api/accounts", json={**base, "surprise": 1}).status_code == 422


def test_providers_and_auth(client, anon_client):
    providers = client.get("/api/accounts/providers").json()["providers"]
    assert {
        "id": "monzo",
        "name": "Monzo",
        "kinds": ["current", "savings", "credit_card"],
    } in providers
    assert anon_client.get("/api/accounts").status_code == 401


def test_plain_messages_and_no_cash_wallet(client):
    a = _person(client)
    base = {"provider": "monzo", "kind": "current", "nickname": "X", "owner_ids": [a]}
    r = client.post("/api/accounts", json={**base, "last4": "12a4"})
    assert r.status_code == 422 and "4 digits" in r.json()["detail"]
    other = next(
        p for p in client.get("/api/accounts/providers").json()["providers"] if p["id"] == "other"
    )
    assert "cash_wallet" not in other["kinds"]
    assert (
        client.post(
            "/api/accounts", json={**base, "provider": "amex", "kind": "savings"}
        ).status_code
        == 201
    )
