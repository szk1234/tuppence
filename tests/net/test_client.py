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
    assert e.outcome == "error" and "refused" in (e.note or "")
