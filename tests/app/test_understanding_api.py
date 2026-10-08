"""The M4 API on the real app (server mode, signed in). No AI model is set up here, so
rows the rules don't cover wait for AI, and the person's corrections and rules do the rest."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from tuppence.ingest.handoff import ANALYSIS_JOB, ANALYSIS_SCOPE, enqueue_analysis
from tuppence.ingest.models import ParsedRow, ParsedStatement

ROWS = [
    (date(2026, 8, 14), -999, "Streamly"),
    (date(2026, 9, 14), -999, "Streamly"),
    (date(2026, 10, 14), -999, "Streamly"),
    (date(2026, 10, 1), -14200, "Northfield Council Council Tax"),
    (date(2026, 10, 12), -450, "Sunrise Bakery"),
]


def analyse(services) -> None:
    services.queue.expedite(ANALYSIS_JOB, scope_key=ANALYSIS_SCOPE)
    while services.worker.run_once():
        pass


def three_months(client, *, run: bool = True):
    services = client.app.state.services
    r = client.post("/api/household/people", json={"display_name": "Alex Example", "role": "adult"})
    owner = r.json()["id"]
    r = client.post(
        "/api/accounts",
        json={"provider": "starling", "kind": "current", "nickname": "Joint", "owner_ids": [owner]},
    )
    account = r.json()["id"]
    record = services.statements.create(sha256="a" * 64, ext="csv", filename="s.csv", kind="csv")
    rows = [
        ParsedRow(
            ref=f"L{n}",
            date=day,
            amount_pence=pence,
            amount_text=f"{-pence / 100:.2f}",
            raw_description=text,
            merchant=text,
        )
        for n, (day, pence, text) in enumerate(ROWS, start=2)
    ]
    parsed = ParsedStatement(
        importer="csv:test", period_start=date(2026, 8, 1), period_end=date(2026, 10, 31), rows=rows
    )
    services.statements.persist(record.id, account, parsed, balance_verified=False, stats={})
    if run:
        enqueue_analysis(services.queue, record.id)
        analyse(services)
    return services


def test_sign_in_is_required(anon_client):
    for path in (
        "/api/spending",
        "/api/commitments",
        "/api/rules",
        "/api/categories",
        "/api/analysis",
        "/api/home/summary",
    ):
        assert anon_client.get(path).status_code == 401


def test_spending_correction_rule_and_commitments(client):
    services = three_months(client)
    top = client.get("/api/spending", params={"on": "2026-10-20"}).json()
    assert top["period"]["label"] == "October 2026"
    tiles = {t["id"]: t["amount"] for t in top["tiles"]}
    assert tiles == {"housing": "142.00", "unsorted": "14.49"}  # the seed council-tax rule
    assert top["waiting_for_ai"] == 4
    unsorted = client.get(
        "/api/spending/transactions", params={"on": "2026-10-20", "category": "unsorted"}
    ).json()
    streamly = next(t for t in unsorted["transactions"] if t["merchant"] == "Streamly")
    why = client.get(f"/api/transactions/{streamly['id']}/why").json()
    assert "Waiting for an AI model" in " ".join(why["steps"])
    fixed = client.patch(
        f"/api/transactions/{streamly['id']}/understanding",
        json={"category_id": "subscriptions.tv-streaming", "expected_version": streamly["version"]},
    )
    assert fixed.status_code == 200, fixed.text
    offer = fixed.json()["rule_offer"]
    assert (offer["merchant_name"], offer["will_change"], offer["kept_yours"]) == ("Streamly", 2, 1)
    again = client.patch(
        f"/api/transactions/{streamly['id']}/understanding",
        json={"category_id": "other", "expected_version": streamly["version"]},
    )
    assert again.status_code == 409
    made = client.post(
        "/api/rules",
        json={
            "merchant_id": offer["merchant_id"],
            "set_category_id": "subscriptions.tv-streaming",
            "created_from_transaction_id": streamly["id"],
        },
    )
    assert made.status_code == 201 and made.json()["changed"] == 2
    assert [
        r["description"] for r in client.get("/api/rules").json()["rules"] if r["source"] == "user"
    ] == ["Payments to Streamly → Subscriptions › TV & video streaming"]
    analyse(services)
    view = client.get("/api/commitments", params={"start": "2026-11-01", "days": 30}).json()
    [sub] = [c for c in view["commitments"] if c["name"] == "Streamly"]
    assert (sub["kind"], sub["cadence"], sub["amount"], sub["next_due"]) == (
        "subscription",
        "monthly",
        "9.99",
        "2026-11-14",
    )
    assert [d["date"] for d in view["upcoming"] if d["name"] == "Streamly"] == ["2026-11-14"]
    hidden = client.post(
        f"/api/commitments/{sub['id']}/dismiss", json={"expected_version": sub["version"]}
    )
    assert hidden.status_code == 200
    assert all(
        c["name"] != "Streamly" for c in client.get("/api/commitments").json()["commitments"]
    )


def test_categories_runs_and_home(client):
    three_months(client)
    cats = client.get("/api/categories").json()["categories"]
    assert cats[0]["id"] == "housing" and {"food.groceries", "transfers.cash"} <= {
        c["id"] for c in cats
    }
    made = client.post("/api/categories", json={"parent_id": "food", "label": "Bakeries"})
    assert made.status_code == 201 and made.json()["level"] == 2
    top_level = client.post("/api/categories", json={"label": "Side project", "kind": "spend"})
    assert top_level.status_code == 201 and top_level.json()["id"] == "side-project"
    status = client.get("/api/analysis").json()
    assert status["last_run"]["status"] == "partial" and status["waiting"]["awaiting_ai"] >= 1
    assert client.post("/api/analysis/run").status_code == 202
    home = client.get("/api/home/summary").json()
    assert home["analysis"]["queued"] and "spent" in home


@pytest.fixture
def member(client):
    """A signed-in household member who is not an admin."""
    client.app.state.services.users.create("member", "another-long-password", is_admin=False)
    m = TestClient(client.app)
    r = m.post("/api/auth/login", json={"username": "member", "password": "another-long-password"})
    assert r.status_code == 200, r.text
    m.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return m


def txn_of(services, text: str) -> dict:
    with services.db.connection() as conn:
        row = conn.execute(
            'SELECT t.id, u.version, u.merchant_id FROM "transaction" t JOIN understanding u'
            " ON u.transaction_id = t.id WHERE t.raw_description = ? ORDER BY t.date",
            [text],
        ).fetchone()
    return dict(row)


def test_members_use_every_understanding_router_but_not_settings_or_manifests(client, member):
    services = three_months(client)
    row = txn_of(services, "Sunrise Bakery")
    # corrections, categories, rules, analysis: household data, open to any signed-in member
    fixed = member.patch(
        f"/api/transactions/{row['id']}/understanding",
        json={"category_id": "food.eating-out", "expected_version": row["version"]},
    )
    assert fixed.status_code == 200, fixed.text
    reset = member.post(
        f"/api/transactions/{row['id']}/understanding/reset",
        json={"expected_version": fixed.json()["understanding"]["version"]},
    )
    assert reset.status_code == 200 and reset.json()["status"] == "unknown"
    made = member.post("/api/categories", json={"parent_id": "food", "label": "Bakeries"})
    assert made.status_code == 201
    renamed = member.patch(
        f"/api/categories/{made.json()['id']}",
        json={"label": "Bakery items", "expected_version": made.json()["version"]},
    )
    assert renamed.status_code == 200
    retired = member.post(
        f"/api/categories/{made.json()['id']}/retire",
        json={"expected_version": renamed.json()["version"]},
    )
    assert retired.status_code == 200
    rule = member.post(
        "/api/rules", json={"text_pattern": "SUNRISE", "set_category_id": "food.eating-out"}
    )
    assert rule.status_code == 201
    off = member.post(
        f"/api/rules/{rule.json()['rule']['id']}/disable",
        json={"expected_version": rule.json()["rule"]["version"]},
    )
    assert off.status_code == 200
    assert member.post("/api/categories/refiles/nothing/undo").status_code == 404  # not 403
    assert member.post("/api/analysis/run").status_code == 202
    streamly = txn_of(services, "Streamly")
    assert (
        member.post(
            "/api/rules",
            json={
                "merchant_id": streamly["merchant_id"],
                "set_category_id": "subscriptions.tv-streaming",
            },
        ).status_code
        == 201
    )
    analyse(services)
    [sub] = [
        c for c in member.get("/api/commitments").json()["commitments"] if c["name"] == "Streamly"
    ]
    assert (
        member.post(
            f"/api/commitments/{sub['id']}/dismiss", json={"expected_version": sub["version"]}
        ).status_code
        == 200
    )
    assert (
        member.post(
            f"/api/commitments/{sub['id']}/restore", json={"expected_version": sub["version"] + 1}
        ).status_code
        == 200
    )
    # settings and agent manifests stay the admin's
    denied = [
        member.patch("/api/settings/llm.run_cap_gbp", json={"value": 1, "expected_version": 0}),
        member.patch("/api/config/agents/categoriser", json={"changes": {}, "expected_version": 0}),
        member.post(
            "/api/config/agents/categoriser/reset", json={"path": "x", "expected_version": 0}
        ),
    ]
    assert [r.status_code for r in denied] == [403, 403, 403]


def test_an_admin_uses_each_router_too(client):
    services = three_months(client)
    row = txn_of(services, "Sunrise Bakery")
    assert (
        client.patch(
            f"/api/transactions/{row['id']}/understanding",
            json={"category_id": "food.eating-out", "expected_version": row["version"]},
        ).status_code
        == 200
    )
    assert (
        client.post("/api/categories", json={"parent_id": "food", "label": "Bakeries"}).status_code
        == 201
    )
    assert client.post("/api/analysis/run").status_code == 202
    assert client.get("/api/commitments").status_code == 200


def test_an_unknown_who_is_a_422_and_never_changes_the_row(client):
    services = three_months(client)
    row = txn_of(services, "Sunrise Bakery")
    r = client.patch(
        f"/api/transactions/{row['id']}/understanding",
        json={"who": "p_nobody", "expected_version": row["version"]},
    )
    assert r.status_code == 422 and r.json()["detail"] == "Choose someone from your household."
    assert services.understanding.get(row["id"]).version == row["version"]
    ok = client.patch(
        f"/api/transactions/{row['id']}/understanding",
        json={"who": "household", "expected_version": row["version"]},
    )
    assert ok.status_code == 200 and ok.json()["understanding"]["who"] == "household"


def test_confirming_a_row_links_its_merchant_so_it_feeds_merchant_memory(client):
    services = three_months(client, run=False)  # nothing has resolved merchants yet
    row = txn_of(services, "Streamly")
    assert row["merchant_id"] is None
    r = client.patch(
        f"/api/transactions/{row['id']}/understanding",
        json={"category_id": "subscriptions.tv-streaming", "expected_version": row["version"]},
    )
    assert r.status_code == 200, r.text
    linked = services.understanding.get(row["id"])
    assert linked.status == "confirmed" and linked.merchant_id is not None
    assert services.merchants.get(linked.merchant_id).name == "Streamly"
    # the knowledge-version bump names the merchant, so the other Streamly rows are looked at
    with services.db.connection() as conn:
        bumped = conn.execute(
            "SELECT merchant_id FROM knowledge_version WHERE kind = 'correction'"
        ).fetchall()
    assert [b["merchant_id"] for b in bumped] == [linked.merchant_id]
    assert r.json()["rule_offer"] is None  # the other Streamly rows aren't resolved until a run
    # a confirmed row that lost its merchant (a re-read) gets it back on the next correction
    with services.db.transaction() as conn:
        conn.execute(
            "UPDATE understanding SET merchant_id = NULL WHERE transaction_id = ?", [row["id"]]
        )
    again = services.understanding.get(row["id"])
    r = client.patch(
        f"/api/transactions/{row['id']}/understanding",
        json={"category_id": "subscriptions.music", "expected_version": again.version},
    )
    assert r.status_code == 200
    assert services.understanding.get(row["id"]).merchant_id == linked.merchant_id


def test_a_stale_version_links_nothing_and_answers_409(client):
    services = three_months(client, run=False)
    row = txn_of(services, "Streamly")
    r = client.patch(
        f"/api/transactions/{row['id']}/understanding",
        json={"category_id": "other", "expected_version": row["version"] + 5},
    )
    assert r.status_code == 409
    assert services.understanding.get(row["id"]).merchant_id is None


def test_ids_are_checked_strictly_and_unknown_ones_are_404(client):
    services = three_months(client)
    row = txn_of(services, "Sunrise Bakery")
    body = {"expected_version": 1}
    for path in (
        "/api/transactions/nope/why",
        "/api/categories/nope/retire",
        "/api/rules/nope/disable",
        "/api/commitments/nope/dismiss",
        "/api/categories/refiles/nope/undo",
        "/api/transactions/nope/understanding/reset",
    ):
        method = client.get if path.endswith("/why") else client.post
        r = method(path) if method == client.get else method(path, json=body)
        assert r.status_code == 404, path
    assert (
        client.patch("/api/categories/nope", json={"label": "x", "expected_version": 1}).status_code
        == 404
    )
    assert client.get("/api/spending", params={"category": "nope"}).status_code == 404
    assert client.get("/api/spending/transactions", params={"category": "nope"}).status_code == 404
    bad_id = "x" * 200
    for r in (
        client.get(f"/api/transactions/{bad_id}/why"),
        client.post(f"/api/rules/{bad_id}/disable", json=body),
        client.get("/api/spending", params={"account_id": "a b;"}),
        client.get("/api/spending", params={"on": "not-a-date"}),
        client.patch(
            f"/api/transactions/{row['id']}/understanding",
            json={"category_id": "x y", "expected_version": 1},
        ),
        client.patch(
            f"/api/transactions/{row['id']}/understanding",
            json={"category_id": "other", "expected_version": 10**30},
        ),
        client.post(
            "/api/rules/preview", json={"text_pattern": "x" * 500, "set_category_id": "other"}
        ),
    ):
        assert r.status_code == 422, r.text


def test_rules_name_things_that_exist(client):
    three_months(client)
    base = {"text_pattern": "BAKERY", "set_category_id": "food.eating-out"}
    for extra in (
        {"merchant_id": "m_nope"},
        {"account_id": "a_nope"},
        {"person_id": "p_nope"},
        {"set_who": "p_nope"},
        {"set_category_id": "nope"},
        {"min_amount": "abc"},
    ):
        for path in ("/api/rules/preview", "/api/rules"):
            r = client.post(path, json={**base, **extra})
            assert r.status_code == 422, (path, extra, r.text)
    r = client.post("/api/rules", json={**base, "created_from_transaction_id": "t_nope"})
    assert r.status_code == 404


def test_version_conflicts_are_409_and_error_text_never_quotes_the_statement(client):
    services = three_months(client)
    cat = client.post("/api/categories", json={"parent_id": "food", "label": "Bakeries"}).json()
    r = client.patch(
        f"/api/categories/{cat['id']}", json={"label": "Cakes", "expected_version": 99}
    )
    assert r.status_code == 409
    rule = client.post(
        "/api/rules", json={"text_pattern": "BAKERY", "set_category_id": "food.eating-out"}
    ).json()["rule"]
    assert (
        client.post(
            f"/api/rules/{rule['id']}/disable", json={"expected_version": rule["version"] + 3}
        ).status_code
        == 409
    )
    [sub] = [
        c for c in client.get("/api/commitments").json()["commitments"] if c["name"] == "Streamly"
    ] or [None]
    row = txn_of(services, "Sunrise Bakery")
    bad = client.patch(
        f"/api/transactions/{row['id']}/understanding",
        json={"category_id": "no.such", "expected_version": row["version"]},
    )
    assert bad.status_code == 422
    for response in (r, bad, client.get(f"/api/transactions/{row['id']}x/why")):
        assert "Sunrise" not in response.text and "Bakery" not in response.text
    if sub is not None:
        assert (
            client.post(
                f"/api/commitments/{sub['id']}/dismiss",
                json={"expected_version": sub["version"] + 9},
            ).status_code
            == 409
        )


def test_the_not_sorted_yet_tile_opens_on_its_own_list(client):
    three_months(client)
    r = client.get("/api/spending", params={"on": "2026-10-20", "category": "unsorted"}).json()
    assert [c["label"] for c in r["path"]] == ["All spending", "Not sorted yet"]
    assert (r["total"], r["direct"], r["tiles"]) == ("14.49", "14.49", [])
