"""The call's timeout is a wall-clock limit for the whole request, not a limit per read."""

import socket
import threading
import time

import httpx
import pytest

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.llm.providers.openai_compat import OpenAICompatProvider
from tuppence.llm.types import ChatRequest, LLMTimeout, Message
from tuppence.net.client import CallContext, make_client
from tuppence.net.privacy_log import PrivacyLog

BODY = (
    b'{"choices":[{"message":{"content":"hi"}}],"usage":{"prompt_tokens":1,"completion_tokens":1}}'
)


@pytest.fixture
def log(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return PrivacyLog(db)


def drip_server(chunks, delay):
    """Answer one request by sending `chunks` one at a time, `delay` seconds apart."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)

    def serve():
        try:
            conn, _ = sock.accept()
        except OSError:
            return
        with conn:
            conn.recv(65536)
            for chunk in chunks:
                time.sleep(delay)
                try:
                    conn.sendall(chunk)
                except OSError:
                    return  # the client gave up, as it should

    threading.Thread(target=serve, daemon=True).start()
    return sock, sock.getsockname()[1]


def padded_reply(pad):
    head = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
    head += b"Content-Length: %d\r\n\r\n" % (len(BODY) + pad)
    return [head] + [b" "] * pad + [BODY]  # leading spaces are valid JSON padding


def client_for(log, timeout):
    ctx = CallContext(purpose="llm", local=True)
    return make_client(ctx, privacy_log=log, local_only=lambda: False, timeout=timeout)


def test_a_body_trickled_slowly_is_cut_off_at_the_call_timeout(log):
    sock, port = drip_server(padded_reply(30), delay=0.1)
    try:
        start = time.monotonic()
        with client_for(log, 1.0) as client, pytest.raises(httpx.ReadTimeout):
            client.get(f"http://127.0.0.1:{port}/v1/models")
        elapsed = time.monotonic() - start
    finally:
        sock.close()
    assert elapsed < 1.6, elapsed
    [entry] = log.list()
    assert entry.outcome == "error" and entry.note == "ReadTimeout"


def test_headers_trickled_slowly_are_cut_off_too(log):
    head = b"HTTP/1.1 200 OK\r\n" + b"X-Pad: 1\r\n" * 40 + b"Content-Length: 2\r\n\r\n{}"
    sock, port = drip_server([head[i : i + 4] for i in range(0, len(head), 4)], delay=0.05)
    try:
        start = time.monotonic()
        with client_for(log, 1.0) as client, pytest.raises(httpx.ReadTimeout):
            client.get(f"http://127.0.0.1:{port}/v1/models")
        assert time.monotonic() - start < 1.6
    finally:
        sock.close()


def test_a_reply_that_finishes_in_time_is_unaffected(log):
    sock, port = drip_server(padded_reply(3), delay=0.05)
    try:
        with client_for(log, 5.0) as client:
            r = client.get(f"http://127.0.0.1:{port}/v1/models")
    finally:
        sock.close()
    assert r.status_code == 200 and r.json()["choices"]


def test_the_provider_reports_a_slow_drip_as_a_timeout(log):
    sock, port = drip_server(padded_reply(30), delay=0.1)
    try:
        start = time.monotonic()
        with client_for(log, 1.0) as client:
            provider = OpenAICompatProvider(client, f"http://127.0.0.1:{port}/v1", None)
            with pytest.raises(LLMTimeout):
                provider.chat(ChatRequest(model="m", messages=[Message(role="user", content="x")]))
        assert time.monotonic() - start < 1.6
    finally:
        sock.close()


def test_each_request_gets_its_own_deadline(log):
    """A client used for several requests (paged model lists) gives each the full timeout."""
    hits = []

    def handler(req):
        hits.append(req.url.path)
        return httpx.Response(200, json={})

    ctx = CallContext(purpose="llm", local=True)
    with make_client(
        ctx,
        privacy_log=log,
        local_only=lambda: False,
        timeout=0.5,
        transport=httpx.MockTransport(handler),
    ) as client:
        for _ in range(3):
            client.get("http://127.0.0.1:9/v1/models")
            time.sleep(0.2)
    assert len(hits) == 3
