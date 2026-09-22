"""Indicator parsing and normalisation.

The indicator type is always derived here, never taken from the client. A URL
submitted as ``{"indicator_type": "domain"}`` would send the wrong enrichment
down the wrong path, and the client has no reason to be trusted with a decision
the server can make correctly.
"""

import ipaddress
import re
from typing import NoReturn
from urllib.parse import urlsplit

MAX_INDICATOR_LENGTH = 2048

_LABEL = r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
_DOMAIN_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*\.[a-zA-Z]{{2,63}}$")

_ALLOWED_SCHEMES = {"http", "https"}


class InvalidIndicator(ValueError):
    """The submitted value is not a domain or URL this platform can process."""


def derive(raw: str) -> tuple[str, str]:
    """Normalise a submitted indicator and classify it.

    Returns ``(indicator, indicator_type)`` where type is ``domain`` or ``url``.
    Raises :class:`InvalidIndicator` with a message safe to return to the client.
    """
    indicator = raw.strip()

    if not indicator:
        raise InvalidIndicator("Indicator must not be empty")
    if len(indicator) > MAX_INDICATOR_LENGTH:
        raise InvalidIndicator(f"Indicator exceeds {MAX_INDICATOR_LENGTH} characters")
    if any(ch.isspace() for ch in indicator):
        raise InvalidIndicator("Indicator must not contain whitespace")

    if "://" in indicator:
        return _derive_url(indicator)

    if not _is_domain(indicator):
        _reject(indicator)
    return indicator.lower(), "domain"


def _derive_url(indicator: str) -> tuple[str, str]:
    parts = urlsplit(indicator)

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise InvalidIndicator(
            f"Unsupported scheme '{parts.scheme}'. Only http and https are accepted."
        )
    if not parts.hostname:
        raise InvalidIndicator("URL is missing a hostname")
    if not _is_domain(parts.hostname):
        raise InvalidIndicator(f"'{parts.hostname}' is not a valid hostname")

    # Lower-case the scheme and host; leave the path and query untouched,
    # since those are case-sensitive.
    normalised = parts._replace(
        scheme=parts.scheme.lower(),
        netloc=parts.netloc.lower(),
    ).geturl()
    return normalised, "url"


def _is_domain(value: str) -> bool:
    return bool(_DOMAIN_RE.match(value)) and len(value) <= 253


def _reject(indicator: str) -> NoReturn:
    """Raise with the most useful message we can infer."""
    try:
        ipaddress.ip_address(indicator)
    except ValueError:
        raise InvalidIndicator(
            f"'{indicator}' is not a valid domain or URL. "
            "Submit a domain such as 'example.com' or a URL such as 'https://example.com/path'."
        ) from None
    raise InvalidIndicator(
        "Bare IP addresses are not accepted as investigation targets. "
        "Submit the domain that resolves to it; IPs are discovered during enrichment."
    )
