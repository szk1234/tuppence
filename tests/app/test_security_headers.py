from fastapi.testclient import TestClient

from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


def test_security_headers_on_every_response(tmp_path):
    client = TestClient(
        create_app(
            RuntimeSettings.for_mode("local", data_dir=tmp_path, allowed_hosts=["testserver"])
        )
    )
    for path in ("/health", "/", "/api/nope"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert "default-src 'self'" in h["content-security-policy"]


def test_no_store_on_api_and_health(tmp_path):
    client = TestClient(
        create_app(
            RuntimeSettings.for_mode("local", data_dir=tmp_path, allowed_hosts=["testserver"])
        )
    )
    assert client.get("/health").headers["cache-control"] == "no-store"
    assert client.get("/api/nope").headers["cache-control"] == "no-store"


def test_unhandled_error_gets_500_json_with_headers(tmp_path, caplog):
    app = create_app(
        RuntimeSettings.for_mode("local", data_dir=tmp_path, allowed_hosts=["testserver"])
    )

    @app.get("/boom-test")
    def boom():
        raise RuntimeError("kaboom")

    app.router.routes.insert(0, app.router.routes.pop())  # ahead of the SPA catch-all

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level("ERROR", logger="tuppence"):
        r = client.get("/boom-test")
    assert r.status_code == 500 and r.json() == {"detail": "Something went wrong."}
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert any("kaboom" in (rec.exc_text or "") or rec.exc_info for rec in caplog.records)
