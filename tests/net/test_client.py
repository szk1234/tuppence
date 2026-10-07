import httpx
import pytest

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.net.client import CallContext, LocalOnlyBlocked, make_client
from tuppence.net.privacy_log import PrivacyLog


@pytest.fixture
def log(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return PrivacyLog(db)


def ok_transport():
    return httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))


def test_logs_sent_calls_without_query_or_body(log):
    ctx = CallContext(purpose="llm", task="coach", connection_id="c1", local=False, redactions=2)
    with make_client(
        ctx, privacy_log=log, local_only=lambda: False, timeout=5, transport=ok_transport()
    ) as c:
        r = c.post("https://api.example.com/v1/chat?key=SECRET", json={"hello": "world"})
    assert r.status_code == 200
    [e] = log.list()
    assert (e.purpose, e.task, e.connection_id, e.destination, e.path, e.outcome, e.redactions) == (
        "llm",
        "coach",
        "c1",
        "api.example.com",
        "/v1/chat",
        "sent",
        2,
    )
    assert e.bytes_out > 0 and e.bytes_in > 0 and e.status == 200
    assert "SECRET" not in e.model_dump_json()


def test_local_only_blocks_cloud_and_logs_block(log):
    ctx = CallContext(purpose="llm", local=False)
    with (
        make_client(
            ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()
        ) as c,
        pytest.raises(LocalOnlyBlocked),
    ):
        c.get("https://api.example.com/v1/models")
    [e] = log.list()
    assert e.outcome == "blocked" and e.bytes_out == 0 and "Local only" in (e.note or "")


def test_local_only_allows_local_connection_on_local_host(log):
    ctx = CallContext(purpose="llm", local=True)
    with make_client(
        ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()
    ) as c:
        assert c.get("http://127.0.0.1:11434/v1/models").status_code == 200


def test_local_flag_alone_is_not_enough_for_public_host(log):
    ctx = CallContext(purpose="llm", local=True)
    with (
        make_client(
            ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()
        ) as c,
        pytest.raises(LocalOnlyBlocked),
    ):
        c.get("http://8.8.8.8/v1/models")


def test_market_data_not_affected_by_local_only(log):
    ctx = CallContext(purpose="market")
    with make_client(
        ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()
    ) as c:
        assert c.get("https://api.example.org/rates").status_code == 200


def test_transport_errors_are_logged_and_reraised(log):
    def boom(req):
        raise httpx.ConnectError("refused", request=req)

    ctx = CallContext(purpose="llm")
    with (
        make_client(
            ctx,
            privacy_log=log,
            local_only=lambda: False,
            timeout=5,
            transport=httpx.MockTransport(boom),
        ) as c,
        pytest.raises(httpx.ConnectError),
    ):
        c.get("https://api.example.com/x")
    [e] = log.list()
    assert e.outcome == "error" and e.note == "ConnectError"


def test_idna_guard_sees_the_wire_host(log, monkeypatch):
    seen = []

    def fake_resolve(host):
        seen.append(host)
        return ["8.8.8.8"]

    monkeypatch.setattr("tuppence.net.hosts._system_resolve", fake_resolve)
    ctx = CallContext(purpose="llm", local=True)
    with (
        make_client(
            ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()
        ) as c,
        pytest.raises(LocalOnlyBlocked),
    ):
        c.get("http://straße.attacker.example/v1")
    assert seen == ["xn--strae-oqa.attacker.example"]
    assert log.list()[0].destination == "xn--strae-oqa.attacker.example"


def test_suffix_name_resolving_public_is_blocked(log, monkeypatch):
    monkeypatch.setattr("tuppence.net.hosts._system_resolve", lambda h: ["8.8.8.8"])
    ctx = CallContext(purpose="llm", local=True)
    with (
        make_client(
            ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()
        ) as c,
        pytest.raises(LocalOnlyBlocked),
    ):
        c.get("http://box.lan/v1")


def test_redirect_hop_to_another_host_is_refused(log):
    def handler(req):
        if req.url.host == "127.0.0.1":
            return httpx.Response(302, headers={"Location": "http://8.8.8.8/steal"})
        return httpx.Response(200)

    ctx = CallContext(purpose="llm", local=True)
    with (
        make_client(
            ctx,
            privacy_log=log,
            local_only=lambda: True,
            timeout=5,
            transport=httpx.MockTransport(handler),
        ) as c,
        pytest.raises(ValueError, match="set up for 127.0.0.1"),
    ):
        # A client serves one hostname, so a hop to another host is refused before connecting.
        c.get("http://127.0.0.1/x", follow_redirects=True)
    assert [e.outcome for e in reversed(log.list())] == ["sent"]


def test_error_note_never_contains_secrets(log):
    def boom(req):
        raise httpx.LocalProtocolError("Illegal header value b'Bearer sk-SECRET\\n'")

    ctx = CallContext(purpose="llm")
    with (
        make_client(
            ctx,
            privacy_log=log,
            local_only=lambda: False,
            timeout=5,
            transport=httpx.MockTransport(boom),
        ) as c,
        pytest.raises(httpx.LocalProtocolError),
    ):
        c.get("https://api.example.com/x", headers={"Authorization": "Bearer sk-SECRET"})
    [e] = log.list()
    assert e.note == "LocalProtocolError"
    assert "SECRET" not in e.model_dump_json()
    with log.db.connection() as conn:
        row = conn.execute("SELECT * FROM privacy_log").fetchone()
    assert "SECRET" not in repr(tuple(row))


def test_real_header_with_newline_is_not_stored(log):
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    ctx = CallContext(purpose="llm", local=True)
    client = make_client(ctx, privacy_log=log, local_only=lambda: False, timeout=5)
    try:
        with client, pytest.raises(httpx.LocalProtocolError):
            client.get(
                f"http://127.0.0.1:{port}/x", headers={"Authorization": "Bearer sk-SECRET\nX: y"}
            )
    finally:
        listener.close()
    [e] = log.list()
    assert e.outcome == "error" and e.note == "LocalProtocolError"
    assert "SECRET" not in e.model_dump_json()


def test_guard_resolves_the_exact_wire_host(log, monkeypatch):
    seen = []
    monkeypatch.setattr("tuppence.net.hosts._system_resolve", lambda h: seen.append(h) or [])
    ctx = CallContext(purpose="llm", local=True)
    for url in ("http://ai./v1", "http://localhost./v1", "http://10.0.0.1%25.evil.example/v1"):
        with (
            make_client(
                ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()
            ) as c,
            pytest.raises(LocalOnlyBlocked),
        ):
            c.get(url)
    assert seen == ["ai.", "localhost.", "10.0.0.1%25.evil.example"]


def test_bracketed_ipv6_literal_through_the_guard(log):
    ctx = CallContext(purpose="llm", local=True)
    for url in ("http://[::1]:11434/v1", "http://[fe80::1]:11434/v1"):
        with make_client(
            ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=ok_transport()
        ) as c:
            assert c.get(url).status_code == 200


def test_log_failure_after_send_does_not_fail_the_call(log, caplog):
    class Broken(PrivacyLog):
        def record(self, event):
            raise RuntimeError("disk full sk-SECRET")

    ctx = CallContext(purpose="llm")
    with make_client(
        ctx,
        privacy_log=Broken(log.db),
        local_only=lambda: False,
        timeout=5,
        transport=ok_transport(),
    ) as c:
        assert c.get("https://api.example.com/x").status_code == 200
    assert "SECRET" not in caplog.text


def test_blocked_path_fails_closed_when_log_fails(log):
    class Broken(PrivacyLog):
        def record(self, event):
            raise RuntimeError("disk full")

    ctx = CallContext(purpose="llm")
    with (
        make_client(
            ctx,
            privacy_log=Broken(log.db),
            local_only=lambda: True,
            timeout=5,
            transport=ok_transport(),
        ) as c,
        pytest.raises(RuntimeError),
    ):
        c.get("https://api.example.com/x")


def test_real_http_transport_to_local_server(log):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"hi")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        ctx = CallContext(purpose="llm", local=True)
        with make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5) as c:
            r = c.get(f"http://127.0.0.1:{srv.server_port}/v1/models")
        assert r.text == "hi"
        assert log.list()[0].bytes_in == 2
    finally:
        srv.shutdown()
        srv.server_close()
