"""SSRF protection for HTTP enrichment.

HTTP enrichment fetches URLs submitted by untrusted users, from a worker
running inside the application's private network. Without protection,
submitting ``http://postgres:5432`` or ``http://169.254.169.254/`` turns this
service into a way to reach infrastructure the submitter cannot otherwise
address.

Two defences, because one is not enough:

1. Every hostname is resolved and every resulting address checked before a
   connection is made.
2. The check is installed *in the resolver used by the HTTP client*, so it
   also runs at connect time. Validating separately and then letting the
   client resolve again leaves a DNS rebinding window: a hostname that
   answered with a public address during validation can answer with
   127.0.0.1 microseconds later, when the connection is actually opened.
"""

import asyncio
import ipaddress
import socket
from typing import Any

import aiohttp

from core.logging import get_logger

log = get_logger(__name__)

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class BlockedAddress(Exception):
    """A hostname resolved to an address this service must not connect to."""


def _unwrap(ip: IpAddress) -> IpAddress:
    """Resolve IPv6 forms that embed an IPv4 address.

    ``::ffff:127.0.0.1`` is loopback, but ``IPv6Address.is_loopback`` only
    recognises ``::1`` — so an embedded address must be unwrapped before it is
    classified. This is a standard way to slip a blocked address past a naive
    check.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        if ip.teredo is not None:
            return ip.teredo[1]
    return ip


def classify_address(ip: IpAddress) -> str | None:
    """Return the reason this address is blocked, or None if it is safe."""
    ip = _unwrap(ip)

    checks: list[tuple[str, bool]] = [
        ("loopback", ip.is_loopback),
        ("link-local", ip.is_link_local),
        ("multicast", ip.is_multicast),
        ("unspecified", ip.is_unspecified),
        ("reserved", ip.is_reserved),
        ("private", ip.is_private),
    ]
    for reason, blocked in checks:
        if blocked:
            return reason
    return None


def assert_address_allowed(raw_ip: str) -> None:
    """Raise :class:`BlockedAddress` if this address must not be fetched."""
    try:
        ip = ipaddress.ip_address(raw_ip)
    except ValueError as exc:
        raise BlockedAddress(f"'{raw_ip}' is not a valid IP address") from exc

    if reason := classify_address(ip):
        raise BlockedAddress(f"{raw_ip} is a {reason} address and will not be fetched")


class ValidatingResolver(aiohttp.abc.AbstractResolver):
    """A resolver that refuses to hand back a blocked address.

    Installed on the client's connector, so it runs for the initial request
    and for every redirect hop, closing the rebinding window described above.

    If *any* returned address is blocked the whole resolution is rejected
    rather than filtered: a host that answers with one public and one private
    address is trying something, and picking the public one would cooperate
    with it.
    """

    def __init__(self) -> None:
        self._inner = aiohttp.DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        infos = await self._inner.resolve(host, port, family)
        for info in infos:
            assert_address_allowed(info["host"])
        return infos

    async def close(self) -> None:
        await self._inner.close()


async def resolve_and_validate(host: str) -> list[str]:
    """Resolve a hostname and return its addresses, rejecting blocked ones.

    A pre-flight check, so a blocked host produces a clear error before any
    connection is attempted and we can record what it resolved to. The
    resolver installed on the connector is what actually guarantees the
    connection target.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BlockedAddress(f"'{host}' did not resolve: {exc}") from exc

    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise BlockedAddress(f"'{host}' did not resolve to any address")
    for address in addresses:
        assert_address_allowed(address)
    return addresses
