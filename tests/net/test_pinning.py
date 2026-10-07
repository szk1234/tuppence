"""The guard decides on resolved addresses and connects to the address it vetted."""

import datetime
import ipaddress
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tuppence.core.db import Database
from tuppence.core.migrate import migrate
from tuppence.net import hosts
from tuppence.net.client import CallContext, LocalOnlyBlocked, make_client
from tuppence.net.privacy_log import PrivacyLog


@pytest.fixture
def log(tmp_path):
    db = Database(tmp_path / "t.db")
    migrate(db, tmp_path / "b")
    return PrivacyLog(db)


class Hello(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"host={self.headers.get('Host')}".encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Hello)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_resolver_is_called_once_per_request(log, monkeypatch):
    seen = []
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: seen.append(h) or ["10.1.2.3"])
    inner = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
    for local_only in (True, False):
        seen.clear()
        ctx = CallContext(purpose="llm", local=True)
        with make_client(
            ctx,
            privacy_log=log,
            local_only=lambda flag=local_only: flag,
            timeout=5,
            transport=inner,
        ) as c:
            c.get("http://lan-box.example/v1")
        assert seen == ["lan-box.example"]


def test_request_goes_to_the_vetted_address_and_keeps_the_name(log, monkeypatch):
    seen = []

    def handler(req):
        seen.append((req.url.host, req.headers["host"]))
        return httpx.Response(200, json={})

    monkeypatch.setattr(hosts, "_system_resolve", lambda h: ["10.1.2.3"])
    ctx = CallContext(purpose="llm", local=True)
    with make_client(
        ctx,
        privacy_log=log,
        local_only=lambda: True,
        timeout=5,
        transport=httpx.MockTransport(handler),
    ) as c:
        c.get("http://lan-box.example:8080/v1")
    assert seen == [("10.1.2.3", "lan-box.example:8080")]
    assert log.list()[0].destination == "lan-box.example"


def test_a_later_resolution_cannot_redirect_the_connection(log, server, monkeypatch):
    answers = {"calls": 0}
    real = socket.getaddrinfo

    def flaky(host, *a, **kw):
        if host == "pinned.test":
            answers["calls"] += 1
            addr = "127.0.0.1" if answers["calls"] == 1 else "169.254.169.254"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))]
        return real(host, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", flaky)
    ctx = CallContext(purpose="llm", local=True)
    with make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5) as c:
        r = c.get(f"http://pinned.test:{server.server_port}/x")
    assert r.status_code == 200 and r.text == f"host=pinned.test:{server.server_port}"
    assert answers["calls"] == 1


def test_unresolvable_name_fails_closed_on_the_real_transport(log, monkeypatch):
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: [])
    ctx = CallContext(purpose="market")
    with (
        make_client(ctx, privacy_log=log, local_only=lambda: False, timeout=5) as c,
        pytest.raises(httpx.ConnectError),
    ):
        c.get("http://nowhere.example/x")


# --- https by name through a pinned address ------------------------------------------------


def _cert(tmp_path, name):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / f"{name}.pem", tmp_path / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture
def tls_server(tmp_path):
    cert, key = _cert(tmp_path, "tls.test")
    srv = HTTPServer(("127.0.0.1", 0), Hello)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    srv.socket = server_ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, cert
    srv.shutdown()
    srv.server_close()


def _tls_client(log, cert):
    trust = ssl.create_default_context(cafile=str(cert))
    inner = httpx.HTTPTransport(verify=trust, retries=0)
    ctx = CallContext(purpose="llm", local=True)
    return make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=inner)


def test_https_by_name_still_verifies_the_name(log, tls_server, monkeypatch):
    srv, cert = tls_server
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: ["127.0.0.1"])
    with _tls_client(log, cert) as c:
        r = c.get(f"https://tls.test:{srv.server_port}/x")
    assert r.status_code == 200 and r.text == f"host=tls.test:{srv.server_port}"


def test_https_to_a_name_the_certificate_does_not_cover_fails(log, tls_server, monkeypatch):
    srv, cert = tls_server
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: ["127.0.0.1"])
    with _tls_client(log, cert) as c, pytest.raises(httpx.ConnectError):
        c.get(f"https://other.test:{srv.server_port}/x")


# --- classification is by address, never by the name string -----------------------------------

BLOCKED = ipaddress.ip_address("169.254.169.254")


def _nat64(prefix_bytes_after):
    return ipaddress.IPv6Address(prefix_bytes_after)


FORMS = [
    "169.254.169.254",
    "2852039166",
    "0xa9fea9fe",
    "169.16689662",
    "[::ffff:169.254.169.254]",
    "[::ffff:a9fe:a9fe]",
    "[64:ff9b::a9fe:a9fe]",
    "[" + str(_nat64(bytes.fromhex("0064ff9b0001a9fe00a9fe0000000000"))) + "]",
    "[fd00:ec2::254]",
    "[fd00:0ec2:0:0:0:0:0:254]",
    "100.100.100.200",
    "metadata.google.internal.",
    "METADATA.GOOGLE.INTERNAL",
]


@pytest.mark.parametrize("purpose", ["llm", "research", "market", "datapack"])
@pytest.mark.parametrize("target", FORMS)
def test_blocked_addresses_are_refused_for_every_purpose(log, purpose, target):
    def reached(req):
        pytest.fail("the request reached the network")

    ctx = CallContext(purpose=purpose, local=True)
    with (
        make_client(
            ctx,
            privacy_log=log,
            local_only=lambda: False,
            timeout=5,
            transport=httpx.MockTransport(reached),
        ) as c,
        pytest.raises(LocalOnlyBlocked),
    ):
        c.get(f"http://{target}/latest")
    assert log.list()[0].outcome == "blocked"


def test_a_name_resolving_to_a_blocked_address_is_refused(log, monkeypatch):
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: ["10.0.0.5", "::ffff:a9fe:a9fe"])
    ctx = CallContext(purpose="market")
    with (
        make_client(
            ctx,
            privacy_log=log,
            local_only=lambda: False,
            timeout=5,
            transport=httpx.MockTransport(lambda r: pytest.fail("reached")),
        ) as c,
        pytest.raises(LocalOnlyBlocked),
    ):
        c.get("http://anything.example/x")


def test_embedded_address_unwrapping():
    assert hosts.embedded_ipv4(ipaddress.ip_address("::ffff:10.1.2.3")) == ipaddress.ip_address(
        "10.1.2.3"
    )
    assert hosts.embedded_ipv4(ipaddress.ip_address("64:ff9b::a00:1")) == ipaddress.ip_address(
        "10.0.0.1"
    )
    local_use = ipaddress.IPv6Address(bytes.fromhex("0064ff9b00010a000000010000000000"))
    assert hosts.embedded_ipv4(local_use) == ipaddress.ip_address("10.0.0.1")
    plain = ipaddress.ip_address("2001:db8::1")
    assert hosts.embedded_ipv4(plain) == plain


def test_real_local_servers_still_work_under_local_only(log, server):
    ctx = CallContext(purpose="llm", local=True)
    for name in ("127.0.0.1", "localhost"):
        with make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5) as c:
            assert c.get(f"http://{name}:{server.server_port}/").status_code == 200


# --- one hostname per client; every vetted address is tried ---------------------------------


def test_a_second_hostname_on_the_same_client_is_refused(log, monkeypatch):
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: ["10.1.2.3"])
    reached = []
    inner = httpx.MockTransport(lambda r: reached.append(r.url.host) or httpx.Response(200))
    ctx = CallContext(purpose="llm", local=True)
    with make_client(
        ctx, privacy_log=log, local_only=lambda: True, timeout=5, transport=inner
    ) as c:
        assert c.get("http://one.example/v1").status_code == 200
        assert c.get("http://one.example/again").status_code == 200
        with pytest.raises(ValueError, match="set up for one.example"):
            c.get("http://two.example/v1")
        with pytest.raises(ValueError):
            c.get("https://one.example/v1")
    assert reached == ["10.1.2.3", "10.1.2.3"]


def test_a_localhost_server_on_ipv6_only_works_under_local_only(log):
    try:
        srv = HTTPServer(("::1", 0), Hello, bind_and_activate=False)
        srv.address_family = socket.AF_INET6
        srv.server_bind()
        srv.server_activate()
    except OSError:
        pytest.skip("no IPv6 loopback")
    srv.address_family = socket.AF_INET6
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ctx = CallContext(purpose="llm", local=True)
        with make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5) as c:
            r = c.get(f"http://localhost:{srv.server_port}/x")
        assert r.status_code == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_next_address_is_used_when_the_first_refuses(log, server, monkeypatch):
    closed = socket.socket()
    closed.bind(("127.0.0.2", 0))
    port_closed = closed.getsockname()[1]
    closed.close()
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: ["127.0.0.2", "127.0.0.1"])
    ctx = CallContext(purpose="llm", local=True)
    # Both addresses share one port number in this check, so serve it on the open address.
    srv = HTTPServer(("127.0.0.1", port_closed), Hello)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with make_client(ctx, privacy_log=log, local_only=lambda: True, timeout=5) as c:
            r = c.get(f"http://box.lan:{port_closed}/x")
        assert r.status_code == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_all_addresses_failing_to_connect_raises_the_last_error(log, monkeypatch):
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: ["127.0.0.2", "127.0.0.3"])
    seen = []

    def refuse(req):
        seen.append(req.url.host)
        raise httpx.ConnectError("refused")

    ctx = CallContext(purpose="llm", local=True)
    with (
        make_client(
            ctx,
            privacy_log=log,
            local_only=lambda: True,
            timeout=5,
            transport=httpx.MockTransport(refuse),
        ) as c,
        pytest.raises(httpx.ConnectError),
    ):
        c.get("http://box.lan/x")
    assert seen == ["127.0.0.2", "127.0.0.3"]


def test_only_connect_errors_fall_through_to_the_next_address(log, monkeypatch):
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: ["127.0.0.2", "127.0.0.3"])
    seen = []

    def read_fails(req):
        seen.append(req.url.host)
        raise httpx.ReadError("dropped")

    ctx = CallContext(purpose="llm", local=True)
    with (
        make_client(
            ctx,
            privacy_log=log,
            local_only=lambda: True,
            timeout=5,
            transport=httpx.MockTransport(read_fails),
        ) as c,
        pytest.raises(httpx.ReadError),
    ):
        c.get("http://box.lan/x")
    assert seen == ["127.0.0.2"]


def test_a_blocked_address_in_the_set_fails_the_whole_request(log, monkeypatch):
    monkeypatch.setattr(hosts, "_system_resolve", lambda h: ["127.0.0.1", "169.254.169.254"])
    ctx = CallContext(purpose="llm", local=True)
    with (
        make_client(
            ctx,
            privacy_log=log,
            local_only=lambda: True,
            timeout=5,
            transport=httpx.MockTransport(lambda r: pytest.fail("reached")),
        ) as c,
        pytest.raises(LocalOnlyBlocked),
    ):
        c.get("http://mixed.lan/x")
