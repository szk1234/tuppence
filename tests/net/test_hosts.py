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
        "ollama",
        "gpu-box.local",
        "nas.lan",
        "host.docker.internal",
        "router.home.arpa",
        "fe80::1",
    ],
)
def test_local_hosts(host):
    assert is_local_host(host, resolve=lambda h: pytest.fail(f"should not resolve {h}"))


@pytest.mark.parametrize("host", ["8.8.8.8", "2606:4700::1111"])
def test_public_ips(host):
    assert not is_local_host(host)


def test_dns_names_resolve_and_must_all_be_private():
    assert is_local_host("box.example.com", resolve=lambda h: ["192.168.1.9"])
    assert not is_local_host("api.openai.com", resolve=lambda h: ["104.18.1.1"])
    assert not is_local_host("mixed.example.com", resolve=lambda h: ["192.168.1.9", "104.18.1.1"])
    assert not is_local_host("nxdomain.example.com", resolve=lambda h: [])
