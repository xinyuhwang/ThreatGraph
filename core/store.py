"""Write helpers shared by the workers.

Every write here targets a uniqueness constraint declared in the schema and
uses ON CONFLICT. That is what makes the workers idempotent: Redis delivers
at-least-once, so a worker will sometimes process the same task twice, and
these statements make the second pass a no-op rather than a duplicate row.

Checking "does this row exist?" before inserting would not be equivalent — two
redelivered copies can both pass the check before either inserts.
"""

from typing import Any
from uuid import UUID

import asyncpg

from core.db import Database
from core.logging import get_logger

log = get_logger(__name__)


async def upsert_entity(
    db: Database,
    entity_type: str,
    value: str,
    metadata: dict[str, Any] | None = None,
) -> UUID:
    """Insert an entity or refresh the one already recorded.

    Entities are shared across investigations, which is what lets the platform
    notice that two domains resolve to the same address. Metadata is merged
    rather than replaced so a later observation cannot erase an earlier one.
    """
    return await db.fetchval(
        """
        INSERT INTO entities (type, value, metadata)
        VALUES ($1, $2, $3)
        ON CONFLICT (type, value) DO UPDATE
           SET last_seen = now(),
               metadata = entities.metadata || EXCLUDED.metadata
        RETURNING id
        """,
        entity_type,
        value,
        # Passed as a dict, not a JSON string: the pool installs a jsonb codec
        # that encodes on the way out and decodes on the way back. Calling
        # json.dumps here would double-encode it into a JSON string.
        metadata or {},
    )


async def save_observation(
    db: Database,
    investigation_id: str,
    source: str,
    data: dict[str, Any],
    *,
    entity_id: UUID | None = None,
    status: str = "ok",
) -> UUID:
    """Record one piece of evidence.

    Unique on (investigation_id, source), so re-running a stage overwrites the
    evidence rather than accumulating copies of it.
    """
    return await db.fetchval(
        """
        INSERT INTO observations (investigation_id, entity_id, source, status, data)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (investigation_id, source) DO UPDATE
           SET data = EXCLUDED.data,
               status = EXCLUDED.status,
               entity_id = EXCLUDED.entity_id,
               collected_at = now()
        RETURNING id
        """,
        investigation_id,
        entity_id,
        source,
        status,
        data,
    )


async def set_enrichment_status(
    db: Database, investigation_id: str, statuses: dict[str, str]
) -> None:
    """Record which sources succeeded.

    The AI analyst reads this to tell "HTTP returned nothing" from "HTTP was
    never attempted" — the distinction that should drive a low-confidence
    unknown rather than a confident conclusion drawn from partial evidence.
    """
    await db.execute(
        "UPDATE investigations SET enrichment_status = $2, updated_at = now() WHERE id = $1",
        investigation_id,
        statuses,
    )


async def create_relationship(
    db: Database,
    source_entity_id: UUID,
    target_entity_id: UUID,
    relationship_type: str,
    confidence: float,
) -> bool:
    """Record a connection between two entities.

    Returns True if this call created the row. Unique on the
    (source, target, type) triple, so a redelivered correlation task cannot
    duplicate an edge.
    """
    if source_entity_id == target_entity_id:
        return False

    created = await db.fetchval(
        """
        INSERT INTO relationships
            (source_entity_id, target_entity_id, relationship_type, confidence)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (source_entity_id, target_entity_id, relationship_type) DO NOTHING
        RETURNING id
        """,
        source_entity_id,
        target_entity_id,
        relationship_type,
        confidence,
    )
    return created is not None


async def get_observations(db: Database, investigation_id: str) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT id, entity_id, source, status, data, collected_at
          FROM observations
         WHERE investigation_id = $1
         ORDER BY source
        """,
        investigation_id,
    )


async def get_observation_map(db: Database, investigation_id: str) -> dict[str, asyncpg.Record]:
    """Observations keyed by source, for callers that want one in particular."""
    return {row["source"]: row for row in await get_observations(db, investigation_id)}
