import pytest

from agents.helpers import AgentEnv, ClientEnv, oracle_handler
from fakes.scripted import Scripted
from knowledge.helpers import KnowledgeEnv
from tuppence.app.services import build_services
from tuppence.settings import RuntimeSettings


@pytest.fixture
def aenv(tmp_path) -> AgentEnv:
    return AgentEnv.create(tmp_path)


@pytest.fixture
def cenv(tmp_path, monkeypatch):
    """The Categoriser on the app's services, its model calls going through the real LLM
    client to a scripted local or cloud server (answered by the oracle). No network."""
    scripted = Scripted()
    scripted.handler = oracle_handler(scripted)
    from tuppence.net import client as netclient

    real = netclient.make_client

    def fake_make_client(ctx, *, privacy_log, local_only, timeout, transport=None):
        return real(
            ctx,
            privacy_log=privacy_log,
            local_only=local_only,
            timeout=timeout,
            transport=scripted.transport(),
        )

    monkeypatch.setattr(netclient, "make_client", fake_make_client)
    services = build_services(RuntimeSettings.for_mode("server", data_dir=tmp_path))
    services.llm.sleep = lambda s: None
    agent = AgentEnv.around(KnowledgeEnv.on(services.db), services.llm)
    agent.window_for = lambda task: services.router.chain_for(task)[0][1].context_window
    yield ClientEnv(agent, services, scripted)
    services.checkpointer.conn.close()
