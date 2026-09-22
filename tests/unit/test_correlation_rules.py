from core.indicators import hostname_of
from workers.correlation import CONFIDENCE


def test_every_confidence_is_in_range():
    assert all(0.0 <= value <= 1.0 for value in CONFIDENCE.values())


def test_confidence_covers_every_rule_the_worker_applies():
    """A new relationship rule must get a deliberate confidence, not a default."""
    assert set(CONFIDENCE) == {"resolves_to", "redirects_to", "hosted_on"}


def test_directly_observed_relationships_score_full_confidence():
    """A resolved A record is not an inference — the resolver said so."""
    assert CONFIDENCE["resolves_to"] == 1.0


def test_hostname_is_shared_between_enrichment_and_correlation():
    """Both stages must derive the same host, or they create two entities
    for one machine and the graph silently splits."""
    assert hostname_of("example.com", "domain") == "example.com"
    assert hostname_of("https://example.com/a/b?c=d", "url") == "example.com"
    assert hostname_of("https://EXAMPLE.com:8443/x", "url") == "example.com"
