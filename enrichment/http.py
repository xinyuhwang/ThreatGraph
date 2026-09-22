"""HTTP evidence collection.

Redirects are followed manually rather than by aiohttp, for two reasons: the
chain itself is evidence worth recording, and each hop must be validated
before it is fetched. Automatic redirects would hide both.
"""

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from core.logging import get_logger
from enrichment.guards import BlockedAddress, ValidatingResolver, resolve_and_validate

log = get_logger(__name__)

MAX_REDIRECTS = 5
MAX_BODY_BYTES = 1_048_576  # 1 MiB
TOTAL_TIMEOUT_SECONDS = 10

REDIRECT_CODES = {301, 302, 303, 307, 308}

USER_AGENT = "ThreatGraph/0.1 (+security-research)"

# Headers worth keeping as evidence. An allowlist rather than the full set:
# response headers can carry large or sensitive values, and these are the ones
# that actually fingerprint a host.
INTERESTING_HEADERS = frozenset(
    {
        "server",
        "content-type",
        "x-powered-by",
        "via",
        "cf-ray",
        "x-frame-options",
        "strict-transport-security",
        "content-security-policy",
        "set-cookie",
        "location",
    }
)

_TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class TooManyRedirects(Exception):
    pass


def _normalise_headers(headers: Any) -> dict[str, str]:
    return {
        key.lower(): value[:512]
        for key, value in headers.items()
        if key.lower() in INTERESTING_HEADERS
    }


def _extract_title(body: bytes) -> str | None:
    match = _TITLE_RE.search(body)
    if not match:
        return None
    try:
        title = match.group(1).decode("utf-8", errors="replace")
    except Exception:
        return None
    return " ".join(title.split())[:200] or None


async def _read_capped(response: aiohttp.ClientResponse) -> tuple[bytes, bool]:
    """Read at most MAX_BODY_BYTES. Returns the body and whether it was cut off."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(16_384):
        chunks.append(chunk)
        total += len(chunk)
        if total >= MAX_BODY_BYTES:
            return b"".join(chunks)[:MAX_BODY_BYTES], True
    return b"".join(chunks), False


async def collect(indicator: str, indicator_type: str) -> dict[str, Any]:
    """Fetch an indicator over HTTP and describe what came back.

    Raises on network failure, timeout, blocked address, or redirect overflow;
    the enrichment worker records those as a failed observation.
    """
    url = indicator if indicator_type == "url" else f"http://{indicator}"

    timeout = aiohttp.ClientTimeout(total=TOTAL_TIMEOUT_SECONDS)
    connector = aiohttp.TCPConnector(resolver=ValidatingResolver(), ssl=False)

    redirect_chain: list[str] = []
    resolved_ips: list[str] = []

    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers={"User-Agent": USER_AGENT},
    ) as session:
        current = url

        for _hop in range(MAX_REDIRECTS + 1):
            host = urlsplit(current).hostname
            if not host:
                raise BlockedAddress(f"'{current}' has no hostname")

            # Pre-flight: produces a clear error and records what the host
            # resolved to. The connector's resolver is the actual guarantee.
            hop_ips = await resolve_and_validate(host)
            resolved_ips = hop_ips

            redirect_chain.append(current)

            async with session.get(current, allow_redirects=False) as response:
                if response.status in REDIRECT_CODES and "location" in response.headers:
                    target = urljoin(current, response.headers["location"])
                    scheme = urlsplit(target).scheme.lower()
                    if scheme not in ("http", "https"):
                        raise BlockedAddress(
                            f"Redirect to unsupported scheme '{scheme}' was refused"
                        )
                    log.debug("following redirect", to=target, status=response.status)
                    current = target
                    continue

                body, truncated = await _read_capped(response)
                return {
                    "requested_url": url,
                    "final_url": current,
                    "status_code": response.status,
                    "redirect_chain": redirect_chain,
                    "redirect_count": len(redirect_chain) - 1,
                    "headers": _normalise_headers(response.headers),
                    "server": response.headers.get("Server"),
                    "content_type": response.headers.get("Content-Type"),
                    "title": _extract_title(body),
                    "body_bytes": len(body),
                    "body_truncated": truncated,
                    "resolved_ips": resolved_ips,
                }

    raise TooManyRedirects(f"Exceeded {MAX_REDIRECTS} redirects starting from {url}")
