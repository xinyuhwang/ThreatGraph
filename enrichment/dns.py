"""DNS evidence collection.

Produces a normalised payload stored as the ``data`` column of a ``dns``
observation. Every field here is something the AI analyst may cite, so the
shape stays flat and predictable.
"""

from typing import Any

import dns.asyncresolver
import dns.exception
import dns.rdatatype
import dns.resolver

from core.logging import get_logger

log = get_logger(__name__)

RECORD_TYPES = ("A", "AAAA", "MX", "NS", "TXT", "CNAME")

TIMEOUT_SECONDS = 5.0


def _build_resolver() -> dns.asyncresolver.Resolver:
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = TIMEOUT_SECONDS
    resolver.lifetime = TIMEOUT_SECONDS
    return resolver


async def _query(resolver: dns.asyncresolver.Resolver, host: str, rtype: str) -> list[str]:
    """Return records of one type. An absent record set is not an error."""
    try:
        answer = await resolver.resolve(host, rtype)
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    return sorted(rdata.to_text().strip('"') for rdata in answer)


async def collect(host: str) -> dict[str, Any]:
    """Collect DNS records for a hostname.

    Raises on timeout or resolver failure — the enrichment worker records that
    as a failed observation. NXDOMAIN is *not* a failure: "this domain does
    not exist" is a real finding, and one the analyst should weigh, so it is
    returned as evidence.
    """
    resolver = _build_resolver()

    try:
        records = {rtype: await _query(resolver, host, rtype) for rtype in RECORD_TYPES}
    except dns.resolver.NXDOMAIN:
        log.info("domain does not exist", host=host)
        return {
            "host": host,
            "nxdomain": True,
            "records": dict.fromkeys(RECORD_TYPES, []),
            "resolved_ips": [],
            "nameservers": [],
        }

    resolved_ips = records["A"] + records["AAAA"]

    return {
        "host": host,
        "nxdomain": False,
        "records": records,
        # Lifted out of `records` because correlation and the analyst both
        # reach for these constantly.
        "resolved_ips": resolved_ips,
        "nameservers": records["NS"],
        "mail_exchangers": records["MX"],
        "spf": [txt for txt in records["TXT"] if txt.lower().startswith("v=spf1")],
    }
