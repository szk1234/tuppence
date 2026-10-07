"""Is a host 'local' (this device or the user's own network)? Spec §4.6."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable

_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8",
        "::1/128",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
        "169.254.0.0/16",
        "fe80::/10",
        "100.64.0.0/10",
    )
)


def _system_resolve(host: str) -> list[str]:
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None)})
    except (OSError, UnicodeError):
        return []


def _local_address(addr: str) -> bool:
    """Explicit allowlist; anything unlisted (6to4, Teredo, NAT64, 198.18/15...) is not local."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in _LOCAL_NETWORKS)


def is_local_host(host: str, *, resolve: Callable[[str], list[str]] | None = None) -> bool:
    """True only for IP literals in the local allowlist, or names whose every address is local.

    Names (including single-label and .local/.lan ones) must resolve; unresolvable is not local.
    """
    h = host.strip().strip("[]").lower().rstrip(".")
    if not h:
        return False
    try:
        ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        if h == "localhost":
            return True
        addresses = (resolve or _system_resolve)(h)
        return bool(addresses) and all(_local_address(a) for a in addresses)
    return _local_address(h)
