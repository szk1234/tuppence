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
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in _LOCAL_NETWORKS)


_METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a) for a in ("169.254.169.254", "fd00:ec2::254", "100.100.100.200")
)
_METADATA_NAMES = frozenset({"metadata.google.internal", "metadata.goog"})


def is_metadata_host(host: str, *, resolve: Callable[[str], list[str]] | None = None) -> bool:
    """Cloud instance-metadata endpoints: never a legitimate target, whatever their locality."""
    h = host.strip().strip("[]").lower()  # resolve exactly what httpcore will connect to
    if not h:
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        if h.rstrip(".") in _METADATA_NAMES:
            return True
        found = (resolve or _system_resolve)(h)
    else:
        found = [str(ip)]
    for addr in found:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip in _METADATA_ADDRESSES:
            return True
    return False


def is_local_host(host: str, *, resolve: Callable[[str], list[str]] | None = None) -> bool:
    """True only for IP literals in the local allowlist, or names whose every address is local.

    Names (including single-label and .local/.lan ones) must resolve; unresolvable is not local.
    """
    # Keep any trailing dot: resolve exactly the name httpcore will connect to.
    h = host.strip().strip("[]").lower()
    if not h:
        return False
    try:
        ipaddress.ip_address(h)
    except ValueError:
        if h == "localhost":
            return True
        addresses = (resolve or _system_resolve)(h)
        return bool(addresses) and all(_local_address(a) for a in addresses)
    return _local_address(h)
