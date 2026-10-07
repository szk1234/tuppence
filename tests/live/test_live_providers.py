"""Live checks against real endpoints. Opt in with `pytest -m live`; never part of CI.

Each test needs its own environment variables and is skipped without them:
  openai     TUPPENCE_LIVE_OPENAI_BASE, TUPPENCE_LIVE_OPENAI_KEY, TUPPENCE_LIVE_OPENAI_MODEL
  anthropic  ANTHROPIC_API_KEY (and optionally TUPPENCE_LIVE_ANTHROPIC_MODEL)
  gemini     GEMINI_API_KEY, TUPPENCE_LIVE_GEMINI_MODEL
  local      TUPPENCE_LIVE_LOCAL_BASE, TUPPENCE_LIVE_LOCAL_MODEL
"""

import os

import pytest
from pydantic import BaseModel

from tuppence.llm.types import Message

pytestmark = pytest.mark.live


class Colour(BaseModel):
    colour: str


def _need(*names: str) -> list[str]:
    values = [os.environ.get(n, "") for n in names]
    if not all(values):
        pytest.skip("needs " + ", ".join(names))
    return values


def _run(client, preset: str, base_url: str | None, key: str | None, model: str) -> None:
    body: dict[str, str] = {"preset": preset}
    if base_url:
        body["base_url"] = base_url
    if key:
        body["api_key"] = key
    r = client.post("/api/llm/connections", json=body)
    assert r.status_code == 201, r.text
    conn = r.json()

    test = client.post(f"/api/llm/connections/{conn['id']}/test").json()
    assert test["ok"], test
    assert test["models"], "the connection listed no models"

    ref = {"connection_id": conn["id"], "model_id": model}
    r = client.patch("/api/settings/llm.simple_model", json={"value": ref, "expected_version": 0})
    assert r.status_code == 200, r.text
    if conn["needs_notice"]:
        client.post(f"/api/llm/connections/{conn['id']}/acknowledge-notice")

    llm = client.app.state.services.llm
    chat = llm.chat("coach", [Message(role="user", content="Say hello in one short line.")])
    assert chat.text.strip()
    out = llm.structured(
        "coach",
        [
            Message(
                role="user", content='What colour is a clear daytime sky? Reply as {"colour": ...}'
            )
        ],
        Colour,
    )
    assert out.colour.strip()


def test_live_openai_compatible(client):
    base, key, model = _need(
        "TUPPENCE_LIVE_OPENAI_BASE", "TUPPENCE_LIVE_OPENAI_KEY", "TUPPENCE_LIVE_OPENAI_MODEL"
    )
    _run(client, "custom", base, key, model)


def test_live_anthropic(client):
    (key,) = _need("ANTHROPIC_API_KEY")
    model = os.environ.get("TUPPENCE_LIVE_ANTHROPIC_MODEL", "claude-haiku-4-5")
    _run(client, "anthropic", None, key, model)


def test_live_gemini(client):
    key, model = _need("GEMINI_API_KEY", "TUPPENCE_LIVE_GEMINI_MODEL")
    _run(client, "gemini", None, key, model)


def test_live_local(client):
    base, model = _need("TUPPENCE_LIVE_LOCAL_BASE", "TUPPENCE_LIVE_LOCAL_MODEL")
    _run(client, "custom", base, None, model)
