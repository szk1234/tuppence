"""A fake OpenAI-compatible server whose replies are scripted per call."""

import json

import httpx

from tuppence.net import client as netclient


class Scripted:
    def __init__(self):
        self.replies = []
        self.requests = []
        self.handler = self._default  # tests may replace this attribute

    def _default(self, req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m-small"}, {"id": "m-big"}]})
        self.requests.append(json.loads(req.content))
        reply = self.replies.pop(0) if self.replies else {"content": "ok"}
        if isinstance(reply, httpx.Response):
            return reply
        if callable(reply):
            return reply(req)
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": reply["content"]}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(lambda req: self.handler(req))  # looked up per request


def install(monkeypatch, scripted: Scripted) -> None:
    """Route every guarded LLM client through the scripted fake (guard and log still run)."""
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
