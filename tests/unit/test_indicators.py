import pytest

from core.indicators import InvalidIndicator, derive


@pytest.mark.parametrize(
    ("raw", "expected_indicator", "expected_type"),
    [
        ("example.com", "example.com", "domain"),
        ("  example.com  ", "example.com", "domain"),
        ("EXAMPLE.COM", "example.com", "domain"),
        ("sub.domain.example.co.uk", "sub.domain.example.co.uk", "domain"),
        ("xn--80ak6aa92e.com", "xn--80ak6aa92e.com", "domain"),
        ("https://example.com/path", "https://example.com/path", "url"),
        ("HTTP://Example.COM/Path", "http://example.com/Path", "url"),
        ("https://example.com/a?b=c#d", "https://example.com/a?b=c#d", "url"),
    ],
)
def test_derives_type_and_normalises(raw, expected_indicator, expected_type):
    indicator, indicator_type = derive(raw)
    assert (indicator, indicator_type) == (expected_indicator, expected_type)


def test_url_path_case_is_preserved():
    """Hostnames are case-insensitive; paths are not."""
    indicator, _ = derive("https://EXAMPLE.com/CaseSensitive")
    assert indicator == "https://example.com/CaseSensitive"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not a domain",
        "example",
        "example.",
        ".example.com",
        "exa mple.com",
        "-example.com",
        "example.com-",
        "example.123",
        "a" * 2049,
    ],
)
def test_rejects_malformed_input(raw):
    with pytest.raises(InvalidIndicator):
        derive(raw)


@pytest.mark.parametrize("raw", ["ftp://example.com", "file:///etc/passwd", "gopher://example.com"])
def test_rejects_non_http_schemes(raw):
    with pytest.raises(InvalidIndicator, match="scheme"):
        derive(raw)


@pytest.mark.parametrize("raw", ["203.0.113.42", "::1", "127.0.0.1"])
def test_rejects_bare_ip_with_a_useful_message(raw):
    """IPs are discovered during enrichment, not submitted as targets."""
    with pytest.raises(InvalidIndicator, match="IP addresses"):
        derive(raw)
