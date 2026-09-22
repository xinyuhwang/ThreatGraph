"""Evidence and entity views.

These are what make a conclusion checkable: given a result, a reader can pull
the observations it cited and the infrastructure graph it was drawn from.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import get_db
from core.db import Database
from core.logging import set_investigation_id

router = APIRouter(prefix="/investigations", tags=["evidence"])


async def _require_investigation(db: Database, investigation_id: UUID) -> None:
    exists = await db.fetchval("SELECT 1 FROM investigations WHERE id = $1", investigation_id)
    if not exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Investigation not found")


@router.get("/{investigation_id}/evidence", summary="Raw enrichment observations")
async def get_evidence(
    investigation_id: UUID,
    source: Annotated[str | None, Query(pattern="^(dns|http|tls|whois)$")] = None,
    obs_status: Annotated[str | None, Query(alias="status", pattern="^(ok|failed)$")] = None,
    db: Database = Depends(get_db),
) -> list[dict[str, Any]]:
    """Every observation collected, successful and failed.

    Failed sources are included deliberately. "DNS was attempted and timed
    out" is evidence; omitting it would leave a reader unable to tell it apart
    from a source that was never tried.
    """
    set_investigation_id(str(investigation_id))
    await _require_investigation(db, investigation_id)

    rows = await db.fetch(
        """
        SELECT o.id, o.source, o.status, o.data, o.collected_at,
               o.entity_id, e.type AS entity_type, e.value AS entity_value
          FROM observations o
          LEFT JOIN entities e ON e.id = o.entity_id
         WHERE o.investigation_id = $1
           AND ($2::text IS NULL OR o.source = $2)
           AND ($3::text IS NULL OR o.status = $3)
         ORDER BY o.source
        """,
        investigation_id,
        source,
        obs_status,
    )
    return [dict(row) for row in rows]


@router.get("/{investigation_id}/entities", summary="Discovered entities and relationships")
async def get_entities(
    investigation_id: UUID,
    relationship_type: Annotated[
        str | None,
        Query(pattern="^(resolves_to|uses_certificate|redirects_to|hosted_on)$"),
    ] = None,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """The entities this investigation touched, and how they connect.

    Relationships are shared across investigations, so an edge here may have
    been recorded by an earlier one — which is exactly how shared
    infrastructure becomes visible.
    """
    set_investigation_id(str(investigation_id))
    await _require_investigation(db, investigation_id)

    # Entities named by an observation, plus everything one relationship hop
    # away. The hop matters: the addresses a domain resolves to are the most
    # interesting things an investigation discovers, and no observation names
    # them directly — they are reached through the edges correlation created.
    #
    # One hop, not more. Two would pull in every other domain sharing an
    # address, which is unbounded on popular hosting. That traversal belongs
    # to the analyst's get_related_entities tool, where it can be limited.
    entities = await db.fetch(
        """
        WITH observed AS (
            SELECT DISTINCT o.entity_id AS id
              FROM observations o
             WHERE o.investigation_id = $1 AND o.entity_id IS NOT NULL
        ),
        reachable AS (
            SELECT id FROM observed
            UNION
            SELECT r.target_entity_id FROM relationships r
              JOIN observed ON r.source_entity_id = observed.id
            UNION
            SELECT r.source_entity_id FROM relationships r
              JOIN observed ON r.target_entity_id = observed.id
        )
        SELECT e.id, e.type, e.value, e.metadata, e.first_seen, e.last_seen
          FROM entities e
          JOIN reachable ON reachable.id = e.id
         ORDER BY e.type, e.value
        """,
        investigation_id,
    )
    entity_ids = [row["id"] for row in entities]

    relationships = await db.fetch(
        """
        SELECT r.id, r.relationship_type, r.confidence, r.observed_at,
               src.id AS source_id, src.type AS source_type, src.value AS source_value,
               tgt.id AS target_id, tgt.type AS target_type, tgt.value AS target_value
          FROM relationships r
          JOIN entities src ON src.id = r.source_entity_id
          JOIN entities tgt ON tgt.id = r.target_entity_id
         WHERE (r.source_entity_id = ANY($1::uuid[]) OR r.target_entity_id = ANY($1::uuid[]))
           AND ($2::text IS NULL OR r.relationship_type = $2)
         ORDER BY r.relationship_type, src.value
        """,
        entity_ids,
        relationship_type,
    )

    return {
        "entities": [dict(row) for row in entities],
        "relationships": [dict(row) for row in relationships],
    }
