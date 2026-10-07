import pytest
from fastapi.testclient import TestClient

from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


@pytest.fixture
def make_app(tmp_path):
    def _make(mode="server", **kw):
        if mode != "server":
            kw.setdefault("allowed_hosts", ["testserver"])
        settings = RuntimeSettings.for_mode(
            mode, data_dir=tmp_path / "data", web_dir=tmp_path / "no-ui", **kw
        )
        return create_app(settings)

    return _make


@pytest.fixture
def anon_client(make_app):
    return TestClient(make_app())


@pytest.fixture
def client(make_app):
    c = TestClient(make_app("server"))
    r = c.post("/api/auth/setup", json={"username": "admin", "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c
