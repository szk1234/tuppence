from tuppence.net.privacy_log import PrivacyEvent


def test_privacy_log_endpoint(client):
    services = client.app.state.services
    services.privacy_log.record(
        PrivacyEvent(
            ts="2026-10-07T00:00:00Z",
            purpose="llm",
            task="coach",
            connection_id=None,
            destination="api.example.com",
            method="POST",
            path="/v1/chat",
            bytes_out=10,
            bytes_in=20,
            status=200,
            redactions=0,
            outcome="sent",
            note=None,
        )
    )
    r = client.get("/api/privacy/log")
    assert r.status_code == 200 and r.json()["entries"][0]["destination"] == "api.example.com"


def test_privacy_log_requires_sign_in(anon_client):
    assert anon_client.get("/api/privacy/log").status_code == 401


def test_privacy_log_paging_is_bounded(client):
    for params in (
        {"before_id": str(2**70)},
        {"before_id": "0"},
        {"limit": str(2**70)},
        {"limit": "0"},
    ):
        assert client.get("/api/privacy/log", params=params).status_code == 422, params
    ok = client.get("/api/privacy/log", params={"limit": "500", "before_id": str(2**63 - 1)})
    assert ok.status_code == 200
