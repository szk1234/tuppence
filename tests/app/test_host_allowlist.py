import pytest
from fastapi.testclient import TestClient

from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


def build(tmp_path, mode, port=0, **kw):
    return create_app(
        RuntimeSettings.for_mode(
            mode, data_dir=tmp_path / "d", port=port, web_dir=tmp_path / "x", **kw
        )
    )


@pytest.mark.parametrize("path", ["/health", "/api/settings", "/auth/launch?token=x", "/"])
def test_rebinding_attempt_rejected_in_desktop_mode(tmp_path, path):
    c = TestClient(build(tmp_path, "desktop", port=8123, launch_token="T" * 43))
    r = c.get(path, headers={"host": "evil.example"})
    assert r.status_code == 403 and r.json() == {"detail": "Unexpected host."}
    assert r.headers["x-frame-options"] == "DENY"
    r2 = c.get(path, headers={"host": "evil.example:8123"})
    assert r2.status_code == 403


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8123", "localhost:8123", "[::1]:8123", "LocalHost:8123"]
)
def test_loopback_with_right_port_passes(tmp_path, host):
    c = TestClient(build(tmp_path, "local", port=8123))
    assert c.get("/health", headers={"host": host}).status_code == 200


def test_wrong_port_and_missing_port_rejected(tmp_path):
    c = TestClient(build(tmp_path, "local", port=8123))
    assert c.get("/health", headers={"host": "127.0.0.1:9999"}).status_code == 403
    assert c.get("/health", headers={"host": "127.0.0.1"}).status_code == 403


def test_portless_loopback_ok_only_on_port_80(tmp_path):
    c = TestClient(build(tmp_path, "local", port=80))
    assert c.get("/health", headers={"host": "localhost"}).status_code == 200


def test_desktop_port_zero_uses_actual_server_port(tmp_path):
    app = build(tmp_path, "desktop", port=0)
    c = TestClient(app, base_url="http://127.0.0.1:5555", client=("127.0.0.1", 1))
    # TestClient reports server=("127.0.0.1", 5555) in the scope.
    assert c.get("/health").status_code == 200
    assert c.get("/health", headers={"host": "127.0.0.1:6666"}).status_code == 403


def test_server_mode_allows_any_host_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("TUPPENCE_ALLOWED_HOSTS", raising=False)
    c = TestClient(build(tmp_path, "server"))
    assert c.get("/health", headers={"host": "anything.example"}).status_code == 200


def test_server_mode_honours_env_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("TUPPENCE_ALLOWED_HOSTS", "money.example, home.lan")
    c = TestClient(build(tmp_path, "server"))
    assert c.get("/health", headers={"host": "money.example:8040"}).status_code == 200
    assert c.get("/health", headers={"host": "home.lan"}).status_code == 200
    assert c.get("/health", headers={"host": "evil.example"}).status_code == 403


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "127.0.0.1:8040", "localhost", "localhost:9999", "[::1]:8040"]
)
def test_server_allowlist_always_accepts_loopback(tmp_path, monkeypatch, host):
    # The Docker HEALTHCHECK probes http://127.0.0.1:8040/health; a configured allow-list
    # must not turn the container unhealthy (a rebinding page never sends a loopback Host).
    monkeypatch.setenv("TUPPENCE_ALLOWED_HOSTS", "money.home.lan")
    c = TestClient(build(tmp_path, "server"))
    assert c.get("/health", headers={"host": host}).status_code == 200


def test_server_allowlist_still_rejects_unlisted_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("TUPPENCE_ALLOWED_HOSTS", "money.home.lan")
    c = TestClient(build(tmp_path, "server"))
    assert c.get("/health", headers={"host": "money.home.lan:8040"}).status_code == 200
    assert c.get("/health", headers={"host": "evil.example"}).status_code == 403
    assert c.get("/health", headers={"host": "192.168.1.20:8040"}).status_code == 403


def test_explicit_list_in_local_mode_keeps_loopback_port_check(tmp_path):
    c = TestClient(build(tmp_path, "local", port=8123, allowed_hosts=["testserver"]))
    assert c.get("/health", headers={"host": "testserver"}).status_code == 200
    assert c.get("/health", headers={"host": "127.0.0.1:8123"}).status_code == 200
    assert c.get("/health", headers={"host": "127.0.0.1:9999"}).status_code == 403
