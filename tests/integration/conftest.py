"""Fixtures for integration tests.

``db`` and ``redis`` come from the suite-level conftest, so the AI evaluation
tier can share them.
"""

import uuid

import pytest

from core.store import save_observation, upsert_entity
from core.streams import TaskStream


@pytest.fixture
async def investigation(db):
    """A complete, enriched investigation with two observations.

    Created directly rather than through the pipeline so the test controls
    exactly what evidence exists.
    """
    investigation_id = await db.fetchval(
        """
        INSERT INTO investigations (indicator, indicator_type, status, enrichment_status)
        VALUES ('grounding-test.example', 'domain', 'analyzing', '{"dns":"ok","http":"ok"}')
        RETURNING id
        """
    )
    entity_id = await upsert_entity(db, "domain", "grounding-test.example")

    dns_id = await save_observation(
        db,
        investigation_id,
        "dns",
        {"host": "grounding-test.example", "nxdomain": False, "resolved_ips": ["8.8.8.8"]},
        entity_id=entity_id,
    )
    http_id = await save_observation(
        db, investigation_id, "http", {"status_code": 200, "title": "Test"}, entity_id=entity_id
    )

    yield {
        "id": str(investigation_id),
        "entity_id": str(entity_id),
        "observation_ids": {str(dns_id), str(http_id)},
    }

    await db.execute("DELETE FROM investigations WHERE id = $1", investigation_id)


@pytest.fixture
async def other_investigation(db):
    """A second investigation, to test that its evidence cannot be cited."""
    investigation_id = await db.fetchval(
        """
        INSERT INTO investigations (indicator, indicator_type, status)
        VALUES ('other-test.example', 'domain', 'complete')
        RETURNING id
        """
    )
    entity_id = await upsert_entity(db, "domain", "other-test.example")
    observation_id = await save_observation(
        db, investigation_id, "dns", {"host": "other-test.example"}, entity_id=entity_id
    )

    yield {"id": str(investigation_id), "observation_id": str(observation_id)}

    await db.execute("DELETE FROM investigations WHERE id = $1", investigation_id)


@pytest.fixture
async def isolated_stream(redis):
    """A private stream and consumer group.

    The live workers run alongside these tests and consume from the real
    streams, so anything published there would be raced away before a test
    could assert on it.
    """
    name = f"test:tasks:{uuid.uuid4().hex[:10]}"
    group = "test-cg"
    await redis.xgroup_create(name, group, id="0", mkstream=True)
    yield name, group
    await redis.delete(name)


@pytest.fixture
async def tasks(redis):
    return TaskStream(redis)
