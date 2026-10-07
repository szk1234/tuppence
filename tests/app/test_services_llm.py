import httpx

from tuppence.app.services import build_services
from tuppence.llm.connections import detect_local
from tuppence.net import client as netclient
from tuppence.settings import RuntimeSettings


def test_client_factory_resolves_make_client_at_call_time(tmp_path, monkeypatch):
    services = build_services(
        RuntimeSettings.for_mode("server", data_dir=tmp_path / "data", web_dir=tmp_path / "ui")
    )
    real = netclient.make_client
    calls = []

    def patched(ctx, **kw):
        calls.append(ctx)

        def handler(req):
            if req.url.port == 11434:
                return httpx.Response(200, json={"data": [{"id": "m"}]})
            raise httpx.ConnectError("refused", request=req)

        return real(ctx, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(netclient, "make_client", patched)  # patched AFTER build_services
    found = detect_local(services.client_factory)
    assert calls and [(d.preset, d.model_count) for d in found] == [("ollama", 1)]
