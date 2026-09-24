"""Deterministic checks on how Claude actually behaves.

Everything else in the suite tests *our* code: the loop, the tools, the
grounding check, persistence. None of it can tell you whether the model reads
the evidence properly, whether the prompt works, or whether it invents facts
when the evidence is thin. Only the live API answers that, so these tests are
the one place the real client is exercised.

They cost money and are not perfectly deterministic, so they are marked
``ai_eval`` and excluded from the default run. Run them deliberately:

    docker compose run --rm api pytest -m ai_eval

Each fixture pins a known evidence set and asserts a property that should hold
regardless of phrasing — a classification band, a confidence direction, or the
absence of invented citations. Asserting on exact wording would make these
fail for no reason.
"""

import pytest

from analyst.agent import analyse
from analyst.factory import resolve_mode
from core.config import settings
from core.store import save_observation, upsert_entity

pytestmark = [
    pytest.mark.ai_eval,
    pytest.mark.skipif(
        not settings.anthropic_api_key.strip(),
        reason="ANTHROPIC_API_KEY is not set; the live analyst cannot be evaluated",
    ),
]


def live_client():
    from analyst.anthropic_client import AnthropicClient

    return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model)


async def build_case(db, indicator: str, enrichment_status: dict, observations: list):
    """Create an investigation with a pinned evidence set."""
    investigation_id = await db.fetchval(
        """
        INSERT INTO investigations (indicator, indicator_type, status, enrichment_status)
        VALUES ($1, 'domain', 'analyzing', $2)
        RETURNING id
        """,
        indicator,
        enrichment_status,
    )
    entity_id = await upsert_entity(db, "domain", indicator)

    observation_ids = set()
    for source, status, data in observations:
        observation_id = await save_observation(
            db, investigation_id, source, data, entity_id=entity_id, status=status
        )
        observation_ids.add(str(observation_id))

    return str(investigation_id), observation_ids


async def test_the_live_client_is_actually_selected():
    """Guards against the whole tier silently passing against the stub."""
    assert resolve_mode() == "anthropic"


async def test_strong_malicious_evidence_is_classified_as_such(db):
    investigation_id, observation_ids = await build_case(
        db,
        "secure-paypal-signin-verify.example",
        {"dns": "ok", "http": "ok"},
        [
            (
                "dns",
                "ok",
                {
                    "host": "secure-paypal-signin-verify.example",
                    "nxdomain": False,
                    "resolved_ips": ["198.51.100.17"],
                    "records": {"A": ["198.51.100.17"], "MX": [], "TXT": []},
                    "registered_days_ago": 1,
                },
            ),
            (
                "http",
                "ok",
                {
                    "status_code": 200,
                    "title": "Sign in to your PayPal account",
                    "final_url": "http://secure-paypal-signin-verify.example/login",
                    "redirect_chain": [
                        "http://secure-paypal-signin-verify.example/",
                        "http://secure-paypal-signin-verify.example/login",
                    ],
                    "headers": {"server": "nginx"},
                    "form_fields": ["email", "password"],
                },
            ),
        ],
    )

    outcome = await analyse(db, investigation_id, live_client())

    assert outcome.result.classification in {"malicious", "suspicious"}
    assert outcome.result.confidence >= 0.6
    assert outcome.result.recommended_action in {"block", "escalate"}
    await db.execute("DELETE FROM investigations WHERE id = $1", investigation_id)


async def test_ordinary_evidence_is_not_called_malicious(db):
    investigation_id, _ = await build_case(
        db,
        "corner-bakery-eval.example",
        {"dns": "ok", "http": "ok"},
        [
            (
                "dns",
                "ok",
                {
                    "host": "corner-bakery-eval.example",
                    "nxdomain": False,
                    "resolved_ips": ["93.184.216.34"],
                    "records": {
                        "A": ["93.184.216.34"],
                        "MX": ["10 mail.corner-bakery-eval.example"],
                        "TXT": ["v=spf1 include:example.net ~all"],
                    },
                    "registered_days_ago": 2190,
                },
            ),
            (
                "http",
                "ok",
                {
                    "status_code": 200,
                    "title": "Corner Bakery - Fresh Bread Daily",
                    "final_url": "http://corner-bakery-eval.example/",
                    "redirect_chain": ["http://corner-bakery-eval.example/"],
                    "headers": {"server": "Apache"},
                },
            ),
        ],
    )

    outcome = await analyse(db, investigation_id, live_client())

    assert outcome.result.classification != "malicious"
    assert outcome.result.recommended_action != "block"
    await db.execute("DELETE FROM investigations WHERE id = $1", investigation_id)


async def test_thin_evidence_produces_unknown_rather_than_a_guess(db):
    """The property that matters most: absent evidence must not become a
    confident verdict."""
    investigation_id, _ = await build_case(
        db,
        "no-evidence-eval.example",
        {"dns": "failed", "http": "failed"},
        [
            ("dns", "failed", {"error": "Timeout", "detail": "resolver timed out"}),
            ("http", "failed", {"error": "Timeout", "detail": "connection timed out"}),
        ],
    )

    outcome = await analyse(db, investigation_id, live_client())

    assert outcome.result.classification == "unknown"
    assert outcome.result.confidence < 0.5
    await db.execute("DELETE FROM investigations WHERE id = $1", investigation_id)


@pytest.mark.parametrize(
    ("indicator", "enrichment_status", "observations"),
    [
        (
            "citation-eval-a.example",
            {"dns": "ok", "http": "failed"},
            [
                ("dns", "ok", {"host": "citation-eval-a.example", "resolved_ips": ["8.8.8.8"]}),
                ("http", "failed", {"error": "Timeout", "detail": "timed out"}),
            ],
        ),
        (
            "citation-eval-b.example",
            {"dns": "ok", "http": "ok"},
            [
                ("dns", "ok", {"host": "citation-eval-b.example", "resolved_ips": ["1.1.1.1"]}),
                ("http", "ok", {"status_code": 404, "title": None}),
            ],
        ),
    ],
)
async def test_every_citation_is_real(db, indicator, enrichment_status, observations):
    """No invention, across every fixture.

    The grounding check would refuse a fabricated ID anyway — this asserts the
    model does not need saving, which is a different and more useful fact.
    """
    investigation_id, observation_ids = await build_case(
        db, indicator, enrichment_status, observations
    )

    outcome = await analyse(db, investigation_id, live_client())

    cited = {str(ref) for ref in outcome.result.evidence_refs}
    assert cited, "a conclusion must cite something"
    assert cited <= observation_ids, f"cited IDs outside the fixture set: {cited - observation_ids}"
    await db.execute("DELETE FROM investigations WHERE id = $1", investigation_id)
