"""Fixtures for tests that need a real PostgreSQL."""

import pytest

from core.config import settings
from core.db import Database
from core.store import save_observation, upsert_entity


@pytest.fixture
async def db():
    database = Database(settings.database_url)
    await database.connect(min_size=1, max_size=3)
    yield database
    await database.close()


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
