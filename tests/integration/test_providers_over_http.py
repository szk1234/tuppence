"""Every provider style, end to end over real HTTP through the guarded client."""

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from fakes.fake_llm import create_fake_app
from tuppence.desktop.launcher import ServerThread, bind_loopback_socket
from tuppence.llm.types import Message


class Result(BaseModel):
    category: str
    confidence: float


@pytest.fixture
def fake():
    sock = bind_loopback_socket()
    server = ServerThread(create_fake_app(), "127.0.0.1", sock.getsockname()[1], sock)
    server.start()
    try:
        server.wait_until_healthy()
        yield server
    finally:
        server.stop()


# (preset, base URL suffix, model id)
CASES = [
    ("custom", "v1", "fake-small"),
    ("ollama", "v1", "fake-small"),
    ("anthropic", "", "claude-fake"),
    ("gemini", "", "gemini-fake"),
]


@pytest.mark.parametrize(("preset", "suffix", "model"), CASES)
def test_provider_over_http(client: TestClient, fake, preset, suffix, model):
    body = {"preset": preset, "base_url": f"{fake.url}{suffix}", "api_key": "k-test"}
    r = client.post("/api/llm/connections", json=body)
    assert r.status_code == 201, r.text
    conn = r.json()

    test = client.post(f"/api/llm/connections/{conn['id']}/test").json()
    assert test["ok"], test
    assert model in {m["model_id"] for m in test["models"]}

    ref = {"connection_id": conn["id"], "model_id": model}
    r = client.patch("/api/settings/llm.simple_model", json={"value": ref, "expected_version": 0})
    assert r.status_code == 200, r.text
    if conn["needs_notice"]:
        r = client.post(
            f"/api/llm/connections/{conn['id']}/acknowledge-notice",
            json={"expected_version": test["connection"]["version"]},
        )
        assert r.status_code == 200, r.text

    llm = client.app.state.services.llm  # type: ignore[attr-defined]
    chat = llm.chat("coach", [Message(role="user", content="Hello over HTTP")])
    assert chat.text == "Echo: Hello over HTTP"
    result = llm.structured("coach", [Message(role="user", content="Classify")], Result)
    assert result == Result(category="test", confidence=1)
