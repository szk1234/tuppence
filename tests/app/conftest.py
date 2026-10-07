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
def client(make_app):
    return TestClient(make_app())
