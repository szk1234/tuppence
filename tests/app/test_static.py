from fastapi.testclient import TestClient

from tuppence.app import create_app
from tuppence.settings import RuntimeSettings


def _fake_ui(tmp_path):
    web = tmp_path / "web_dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(
        '<!doctype html><div id="app"></div><script type="module" src="/assets/app.js"></script>',
        encoding="utf-8",
    )
    (web / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    (web / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    return web


def client_for(tmp_path, web_dir):
    settings = RuntimeSettings.for_mode("local", data_dir=tmp_path / "data", web_dir=web_dir)
    return TestClient(create_app(settings))


def test_serves_index_and_assets(tmp_path):
    c = client_for(tmp_path, _fake_ui(tmp_path))
    r = c.get("/")
    assert r.status_code == 200 and 'id="app"' in r.text
    a = c.get("/assets/app.js")
    assert a.status_code == 200 and "javascript" in a.headers["content-type"]
    assert c.get("/favicon.svg").status_code == 200


def test_spa_fallback_for_client_routes(tmp_path):
    c = client_for(tmp_path, _fake_ui(tmp_path))
    r = c.get("/settings/ai")
    assert r.status_code == 200 and 'id="app"' in r.text


def test_api_paths_never_fall_back_to_spa(tmp_path):
    c = client_for(tmp_path, _fake_ui(tmp_path))
    r = c.get("/api/unknown")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_missing_asset_is_404_not_index(tmp_path):
    c = client_for(tmp_path, _fake_ui(tmp_path))
    assert c.get("/assets/missing.js").status_code == 404


def test_path_traversal_blocked(tmp_path):
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    c = client_for(tmp_path, _fake_ui(tmp_path))
    r = c.get("/assets/../../secret.txt")
    assert "nope" not in r.text


def test_ui_not_built_page_is_helpful_200(tmp_path):
    c = client_for(tmp_path, tmp_path / "does-not-exist")
    r = c.get("/")
    assert r.status_code == 200
    assert "web UI hasn't been built" in r.text
    assert c.get("/health").status_code == 200
    assert c.get("/some/route").status_code == 200
