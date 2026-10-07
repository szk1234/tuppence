"""Every money field at the API takes text only, by the shared rule in core/money.py."""

import pytest


def _person(client):
    return client.post(
        "/api/household/people", json={"display_name": "Alex Example", "role": "adult"}
    ).json()["id"]


def _posts(client):
    pid = _person(client)
    card = {
        "provider": "barclaycard",
        "kind": "credit_card",
        "nickname": "Card",
        "owner_ids": [pid],
    }
    income = {
        "person_id": pid,
        "kind": "salary",
        "name": "Pay",
        "pay_rule": {"type": "last_working_day"},
    }
    pcp = {"kind": "car_finance_pcp", "lender": "Motor Co", "balance": "1"}
    return [
        ("/api/accounts", card, ("credit_limit",)),
        ("/api/income", income, ("net_amount",)),
        ("/api/debts", {"kind": "personal_loan", "lender": "Bank"}, ("balance",)),
        ("/api/debts", {**pcp, "monthly_payment": "1"}, ("monthly_payment",)),
        ("/api/debts", pcp, ("details", "balloon")),
        ("/api/debts", pcp, ("details", "total_payable")),
        ("/api/goals", {"name": "Fund", "kind": "other"}, ("target_amount",)),
        ("/api/goals", {"name": "Fund", "kind": "other"}, ("saved_amount",)),
    ]


def _with(body, path, value):
    if len(path) == 2:
        return {**body, path[0]: {**body.get(path[0], {}), path[1]: value}}
    return {**body, path[0]: value}


@pytest.mark.parametrize("bad", ["12,34", "1,2,3", ",5", "1 450", 1500, 12.5])
def test_money_fields_refuse_misplaced_commas_and_numbers(client, bad):
    for url, body, path in _posts(client):
        r = client.post(url, json=_with(body, path, bad))
        assert r.status_code == 422, (url, path, bad, r.text)
        detail = r.json()["detail"]
        assert isinstance(detail, str) and "Input should be" not in detail, (url, path, detail)


def test_money_fields_accept_thousands_separators(client):
    for url, body, path in _posts(client):
        r = client.post(url, json=_with(body, path, "1,450.50"))
        assert r.status_code == 201, (url, path, r.text)


def test_timeline_housing_amount_uses_the_same_rule(client):
    entry = {
        "subject_type": "household",
        "subject_id": "1",
        "attribute": "housing_monthly_pence",
        "valid_from": "2026-01-01",
    }
    bad = client.post("/api/household/timeline", json={**entry, "value": "12,34"})
    assert bad.status_code == 422
    ok = client.post("/api/household/timeline", json={**entry, "value": "1,450"})
    assert ok.status_code == 201 and ok.json()["value"] == "1450.00"
