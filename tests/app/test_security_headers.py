from fastapi.testclient import TestClient

from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


def test_security_headers_on_every_response(tmp_path):
    client = TestClient(create_app(RuntimeSettings.for_mode("local", data_dir=tmp_path)))
    for path in ("/health", "/", "/api/nope"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert "default-src 'self'" in h["content-security-policy"]


def test_no_store_on_api_and_health(tmp_path):
    client = TestClient(create_app(RuntimeSettings.for_mode("local", data_dir=tmp_path)))
    assert client.get("/health").headers["cache-control"] == "no-store"
    assert client.get("/api/nope").headers["cache-control"] == "no-store"
