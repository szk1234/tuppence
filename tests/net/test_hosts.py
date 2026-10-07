import pytest

from tuppence.net.hosts import is_local_host


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "localhost",
        "::1",
        "[::1]",
        "192.168.1.20",
        "10.0.0.5",
        "172.16.4.4",
        "100.100.1.1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "fc00::5",
    ],
)
def test_local_literals(host):
    assert is_local_host(host, resolve=lambda h: pytest.fail(f"should not resolve {h}"))


def test_localhost_needs_no_dns():
    assert is_local_host("localhost", resolve=lambda h: pytest.fail("should not resolve"))


@pytest.mark.parametrize(
    "host",
    [
        "ollama",
        "gpu-box.local",
        "nas.lan",
        "host.docker.internal",
        "router.home.arpa",
        "x.internal",
    ],
)
def test_local_names_must_resolve_private(host):
    assert is_local_host(host, resolve=lambda h: ["192.168.1.9"])
    assert not is_local_host(host, resolve=lambda h: ["8.8.8.8"])
    assert not is_local_host(host, resolve=lambda h: [])


@pytest.mark.parametrize("host", ["134744072", "0x08080808", "127.1"])
def test_numeric_forms_are_classified_by_real_address(host):
    assert not is_local_host(host, resolve=lambda h: ["8.8.8.8"])
    assert is_local_host(host, resolve=lambda h: ["127.0.0.1"])


@pytest.mark.parametrize(
    "host",
    [
        "2002:808:808::1",
        "2001:0:4136:e378:8000:63bf:f7f7:f7f7",
        "64:ff9b::808:808",
        "64:ff9b:1::808:808",
        "198.18.0.1",
        "0.0.0.0",
        "::",
        "ff02::1",
        "::ffff:8.8.8.8",
        "240.0.0.1",
        "192.0.2.1",
    ],
)
def test_non_allowlisted_literals_are_not_local(host):
    assert not is_local_host(host)


def test_resolver_swallows_os_and_unicode_errors(monkeypatch):
    import socket

    def boom(*a, **k):
        raise UnicodeError("label too long")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert not is_local_host("ss.example.com")


@pytest.mark.parametrize("host", ["8.8.8.8", "2606:4700::1111"])
def test_public_ips(host):
    assert not is_local_host(host)


def test_dns_names_resolve_and_must_all_be_private():
    assert is_local_host("box.example.com", resolve=lambda h: ["192.168.1.9"])
    assert not is_local_host("api.openai.com", resolve=lambda h: ["104.18.1.1"])
    assert not is_local_host("mixed.example.com", resolve=lambda h: ["192.168.1.9", "104.18.1.1"])
    assert not is_local_host("nxdomain.example.com", resolve=lambda h: [])
