"""Search across investigations, entities, and analyst explanations.

Two index types doing two different jobs. Indicator lookup is substring and
fuzzy matching — someone hunting for `paypal` wants `paypal-secure-login.com`
— which is what trigram indexes are for. Postgres full-text search would
tokenise a domain badly and miss it. Full-text search is reserved for the
analyst's explanations, the only natural language the system stores.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from core.db import Database

router = APIRouter(tags=["search"])

SEARCH_QUERY = """
WITH matched AS (
    SELECT i.id,
           -- Ranked by how closely the indicator itself matches, so an exact
           -- domain hit outranks one that merely mentions the term in prose.
           GREATEST(
               similarity(i.indicator, $1),
               CASE WHEN e.value IS NOT NULL THEN similarity(e.value, $1) * 0.9 ELSE 0 END,
               CASE WHEN r.explanation IS NOT NULL
                     AND to_tsvector('english', r.explanation)
                         @@ plainto_tsquery('english', $1)
                    THEN 0.3 ELSE 0 END
           ) AS rank
      FROM investigations i
      LEFT JOIN investigation_results r ON r.investigation_id = i.id
      LEFT JOIN observations o ON o.investigation_id = i.id
      LEFT JOIN entities e ON e.id = o.entity_id
     WHERE i.indicator ILIKE '%' || $1 || '%'
        OR e.value ILIKE '%' || $1 || '%'
        OR (r.explanation IS NOT NULL
            AND to_tsvector('english', r.explanation) @@ plainto_tsquery('english', $1))
)
SELECT i.id, i.indicator, i.indicator_type, i.status, i.created_at,
       r.classification, r.confidence, r.explanation,
       max(m.rank) AS rank
  FROM matched m
  JOIN investigations i ON i.id = m.id
  LEFT JOIN investigation_results r ON r.investigation_id = i.id
 WHERE ($2::text IS NULL OR i.indicator_type = $2)
   AND ($3::text IS NULL OR i.status = $3)
   AND ($4::text IS NULL OR r.classification = $4)
 GROUP BY i.id, r.id
 ORDER BY max(m.rank) DESC, i.created_at DESC
 LIMIT $5 OFFSET $6
"""

ENTITY_QUERY = """
SELECT id, type, value, first_seen, last_seen, similarity(value, $1) AS rank
  FROM entities
 WHERE value ILIKE '%' || $1 || '%'
 ORDER BY similarity(value, $1) DESC, value
 LIMIT $2
"""


@router.get("/search", summary="Search investigations and entities")
async def search(
    q: Annotated[str, Query(min_length=1, max_length=200, description="Search term.")],
    type: Annotated[str | None, Query(pattern="^(domain|url)$")] = None,
    status: Annotated[
        str | None,
        Query(pattern="^(pending|enriching|analyzing|correlating|complete|failed|cancelled)$"),
    ] = None,
    classification: Annotated[
        str | None, Query(pattern="^(benign|suspicious|malicious|unknown)$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Find investigations by indicator, by an entity they touched, or by what
    the analyst said about them."""
    term = q.strip()

    investigations = await db.fetch(SEARCH_QUERY, term, type, status, classification, limit, offset)
    entities = await db.fetch(ENTITY_QUERY, term, limit)

    return {
        "query": term,
        "investigations": [dict(row) for row in investigations],
        "entities": [dict(row) for row in entities],
        "counts": {
            "investigations": len(investigations),
            "entities": len(entities),
        },
    }
