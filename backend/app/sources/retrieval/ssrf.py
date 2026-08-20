"""SSRF guard for evidence retrieval.

Every fetched URL must be a public HTTP(S) target: literal private/internal
addresses are rejected, and hostnames are resolved and verified to map ONLY
to public IPs (any private hit fails the whole URL, so DNS-rebinding and
mixed public/private answers are both blocked). Resolution failures fail
closed. Redirects are re-validated on every hop by the transport.

The resolver is injectable so tests stay fully offline; production uses
``socket.getaddrinfo`` with a per-run cache.
"""

from __future__ import annotations

import functools
import ipaddress
import socket
from urllib.parse import urlparse

from app.sources.retrieval.models import RetrievalError, RetrievalErrorKind

# Non-routable / internal IPv4 networks (RFC 1918, loopback, link-local,
# CGNAT, documentation, benchmarking, multicast, reserved).
_PRIVATE_V4_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
]

# Non-routable / internal IPv6 networks. IPv4-mapped addresses (::ffff:a.b.c.d)
# are classified through the IPv4 rules via ``ipv4_mapped``.
_PRIVATE_V6_NETWORKS = [
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("2001:db8::/32"),
    ipaddress.ip_network("64:ff9b::/96"),
]

_LOCALHOST_NAMES = frozenset({"localhost", "localhost.localdomain"})


def _is_private_ipv4(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(ip in network for network in _PRIVATE_V4_NETWORKS)


def _is_private_ipv6(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_private_ipv4(str(ip.ipv4_mapped))
    return any(ip in network for network in _PRIVATE_V6_NETWORKS)


def is_private_ip(address: str) -> bool:
    """True when the IP literal is loopback/internal/non-routable."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv4Address):
        return _is_private_ipv4(address)
    return _is_private_ipv6(address)


@functools.lru_cache(maxsize=512)
def default_resolver(host: str) -> list[str]:
    """Resolve a hostname to its (deduplicated) IP literals via getaddrinfo."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        literal = info[4][0]
        if literal not in seen:
            seen.add(literal)
            addresses.append(literal)
    return addresses


def assert_public_http_url(url: str, resolver=None) -> None:
    """Raise RetrievalError(UNSAFE_URL) when the URL is not a public HTTP(S)
    target. `resolver(host) -> list[str]` is injectable for offline tests;
    defaults to real DNS resolution with caching."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RetrievalError(
            RetrievalErrorKind.UNSAFE_URL,
            f"unsafe URL scheme '{parsed.scheme or '(none)'}' for {url}",
        )
    host = parsed.hostname or ""
    if not host:
        raise RetrievalError(
            RetrievalErrorKind.UNSAFE_URL,
            f"URL has no host: {url}",
        )
    if host.casefold() in _LOCALHOST_NAMES:
        raise RetrievalError(
            RetrievalErrorKind.UNSAFE_URL,
            f"blocked internal hostname '{host}' for {url}",
        )
    if is_private_ip(host):
        raise RetrievalError(
            RetrievalErrorKind.UNSAFE_URL,
            f"blocked non-public IP '{host}' for {url}",
        )
    if "." not in host:
        raise RetrievalError(
            RetrievalErrorKind.UNSAFE_URL,
            f"blocked single-label hostname '{host}' for {url}",
        )
    lookup = resolver if resolver is not None else default_resolver
    addresses = lookup(host)
    if not addresses:
        raise RetrievalError(
            RetrievalErrorKind.UNSAFE_URL,
            f"cannot resolve '{host}' for {url}; refusing to fetch",
        )
    for address in addresses:
        if is_private_ip(address):
            raise RetrievalError(
                RetrievalErrorKind.UNSAFE_URL,
                f"blocked non-public address '{address}' for host '{host}' "
                f"({url})",
            )