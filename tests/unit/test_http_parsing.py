import pytest

from enrichment.http import _extract_title, _normalise_headers


class FakeHeaders(dict):
    """Stands in for aiohttp's multidict, which shares dict's items()."""


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"<html><head><title>Login</title></head></html>", "Login"),
        (b"<TITLE>Shouty</TITLE>", "Shouty"),
        (b'<title class="x">With attrs</title>', "With attrs"),
        (b"<title>\n  collapsed\n   whitespace\n</title>", "collapsed whitespace"),
        (b"<html><body>no title</body></html>", None),
        (b"<title></title>", None),
    ],
)
def test_extracts_page_title(body, expected):
    assert _extract_title(body) == expected


def test_title_is_length_capped():
    body = b"<title>" + b"x" * 500 + b"</title>"
    assert len(_extract_title(body)) == 200


def test_invalid_utf8_does_not_crash_extraction():
    assert _extract_title(b"<title>caf\xff\xfe</title>") is not None


def test_keeps_only_fingerprinting_headers():
    headers = FakeHeaders(
        {
            "Server": "nginx/1.25",
            "X-Powered-By": "PHP/7.4.3",
            "Content-Type": "text/html",
            "Date": "Mon, 22 Sep 2026 00:00:00 GMT",
            "Content-Length": "1234",
            "X-Random-Vendor-Header": "noise",
        }
    )
    assert _normalise_headers(headers) == {
        "server": "nginx/1.25",
        "x-powered-by": "PHP/7.4.3",
        "content-type": "text/html",
    }


def test_header_names_are_lowercased():
    assert "server" in _normalise_headers(FakeHeaders({"SeRvEr": "x"}))


def test_oversized_header_values_are_truncated():
    result = _normalise_headers(FakeHeaders({"Set-Cookie": "a" * 2000}))
    assert len(result["set-cookie"]) == 512
