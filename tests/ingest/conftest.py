import json
from pathlib import Path

import httpx
import pytest
from evals import oracle

from fakes.scripted import Scripted
from tuppence.app.services import build_services
from tuppence.settings import RuntimeSettings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "statements"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def oracle_handler(scripted: Scripted):
    """Scripted replies first ({"content": ...}), then the oracle. Model lists as before."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return scripted._default(request)
        body = json.loads(request.content)
        scripted.requests.append(body)
        if scripted.replies:
            reply = scripted.replies.pop(0)
            if isinstance(reply, httpx.Response):  # a scripted HTTP failure
                return reply
            content = reply["content"]
        else:
            content = oracle.reply(body["messages"])
        return httpx.Response(
            200,
            json={
                "model": body.get("model", "m"),
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    return handle


@pytest.fixture
def ingest_env(tmp_path, monkeypatch):
    """(services, scripted): the real app core, with every AI call answered by the oracle."""
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
    yield services, scripted
    checkpointer = getattr(services, "checkpointer", None)  # added in Task 9
    if checkpointer is not None:
        checkpointer.conn.close()
