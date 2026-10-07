"""Is a host 'local' (this device or the user's own network)? Spec §4.6."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable

_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localhost")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _system_resolve(host: str) -> list[str]:
    try:
        return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []


def _private(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 4 and ip in _CGNAT:
        return True
    return ip.is_loopback or ip.is_private or ip.is_link_local


def is_local_host(host: str, *, resolve: Callable[[str], list[str]] | None = None) -> bool:
    h = host.strip().strip("[]").lower().rstrip(".")
    if not h:
        return False
    if h == "localhost" or h.endswith(_SUFFIXES):
        return True
    try:
        ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        if "." not in h:
            return True  # single-label names: Docker services, hosts on the LAN
        addresses = (resolve or _system_resolve)(h)
        return bool(addresses) and all(_private(a) for a in addresses)
    return _private(h)
