import ipaddress

import pytest

from enrichment.guards import BlockedAddress, assert_address_allowed, classify_address


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        # Loopback — reaching the worker's own services.
        ("127.0.0.1", "loopback"),
        ("127.255.255.254", "loopback"),
        ("::1", "loopback"),
        # RFC1918 — the Compose network itself lives here.
        ("10.0.0.1", "private"),
        ("172.16.5.4", "private"),
        ("192.168.1.1", "private"),
        # Cloud instance metadata. The reason this check exists.
        ("169.254.169.254", "link-local"),
        ("fe80::1", "link-local"),
        # Carrier-grade NAT and other reserved space.
        ("0.0.0.0", "unspecified"),
        ("240.0.0.1", "reserved"),
        ("224.0.0.1", "multicast"),
    ],
)
def test_blocks_addresses_that_reach_internal_infrastructure(address, reason):
    assert classify_address(ipaddress.ip_address(address)) == reason


@pytest.mark.parametrize(
    "address",
    [
        # IPv6 forms that embed an IPv4 address. IPv6Address.is_loopback only
        # recognises ::1, so without unwrapping these slip straight through.
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "::ffff:10.0.0.1",
        "::ffff:192.168.1.1",
        # 6to4 wrapping a private address.
        "2002:0a00:0001::",
    ],
)
def test_blocks_ipv4_addresses_smuggled_inside_ipv6(address):
    """The classic bypass: wrap a blocked IPv4 address in an IPv6 form."""
    assert classify_address(ipaddress.ip_address(address)) is not None


@pytest.mark.parametrize(
    "address",
    ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"],
)
def test_allows_public_addresses(address):
    assert classify_address(ipaddress.ip_address(address)) is None


@pytest.mark.parametrize(
    "address",
    ["192.0.2.1", "198.51.100.1", "203.0.113.42", "2001:db8::1"],
)
def test_blocks_documentation_ranges(address):
    """RFC 5737 / RFC 3849 ranges are not globally routable, so a host that
    resolves to one is either misconfigured or pointing somewhere it should
    not. These are the addresses used in this project's own documentation."""
    assert classify_address(ipaddress.ip_address(address)) is not None


def test_assert_raises_with_the_reason():
    with pytest.raises(BlockedAddress, match="link-local"):
        assert_address_allowed("169.254.169.254")


def test_assert_accepts_public_address():
    assert_address_allowed("8.8.8.8")


def test_garbage_is_rejected_not_passed_through():
    with pytest.raises(BlockedAddress, match="not a valid IP"):
        assert_address_allowed("not-an-ip")
