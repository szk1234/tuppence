import pytest

from fakes.scripted import Scripted, install
from tuppence.app.services import build_services
from tuppence.settings import RuntimeSettings


@pytest.fixture
def env(tmp_path, monkeypatch):
    scripted = Scripted()
    services = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    install(monkeypatch, scripted)
    services.llm.sleep = lambda s: scripted.requests.append({"slept": s})
    return services, scripted
