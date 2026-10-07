from fastapi.testclient import TestClient

from tuppence import __version__
from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


def make_client(tmp_path, mode="local"):
    settings = RuntimeSettings.for_mode(
        mode, data_dir=tmp_path, web_dir=tmp_path / "no-ui", allowed_hosts=["testserver"]
    )
    return TestClient(create_app(settings))


def test_health_reports_version_and_mode(tmp_path):
    client = make_client(tmp_path, mode="server")
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__, "mode": "server"}


def test_health_works_with_unicode_data_dir(tmp_path):
    client = make_client(tmp_path / "Zoë Smith" / "App Data")
    assert client.get("/health").status_code == 200


def test_unknown_api_route_is_json_404(tmp_path):
    r = make_client(tmp_path).get("/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_mode_defaults():
    from pathlib import Path

    assert RuntimeSettings.for_mode("server", data_dir=Path("/x")).host == "0.0.0.0"
    assert RuntimeSettings.for_mode("server", data_dir=Path("/x")).port == 8040
    assert RuntimeSettings.for_mode("local", data_dir=Path("/x")).host == "127.0.0.1"
    assert RuntimeSettings.for_mode("desktop", data_dir=Path("/x")).port == 0
