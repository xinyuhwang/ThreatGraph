"""Investigation submission and retrieval."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import get_db, get_tasks
from api.models import AnalysisResult, InvestigationCreated, InvestigationDetail
from api.models import SubmitInvestigation as SubmitBody
from api.ratelimit import enforce_rate_limit
from core.db import Database
from core.indicators import InvalidIndicator, derive
from core.logging import get_logger, set_investigation_id
from core.streams import STREAM_ENRICH, TaskStream

log = get_logger(__name__)

router = APIRouter(prefix="/investigations", tags=["investigations"])

# Pulls the investigation plus its result, with evidence references collected
# from the junction table into a single array.
DETAIL_QUERY = """
SELECT i.id, i.indicator, i.indicator_type, i.status, i.enrichment_status,
       i.error, i.created_at, i.updated_at,
       r.classification, r.confidence, r.explanation, r.entity_ids,
       r.recommended_action,
       COALESCE(
           array_agg(re.observation_id) FILTER (WHERE re.observation_id IS NOT NULL),
           ARRAY[]::uuid[]
       ) AS evidence_refs
  FROM investigations i
  LEFT JOIN investigation_results r ON r.investigation_id = i.id
  LEFT JOIN result_evidence re ON re.result_id = r.id
 WHERE i.id = $1
 GROUP BY i.id, r.id
"""


@router.post(
    "",
    response_model=InvestigationCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Submit an indicator for investigation",
)
async def submit(
    body: SubmitBody,
    db: Database = Depends(get_db),
    tasks: TaskStream = Depends(get_tasks),
    _: None = Depends(enforce_rate_limit),
) -> InvestigationCreated:
    """Accept an indicator and return immediately.

    Nothing blocks on enrichment: the record is saved, a task is published, and
    the client polls for the result.
    """
    try:
        indicator, indicator_type = derive(body.indicator)
    except InvalidIndicator as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    row = await db.fetchrow(
        """
        INSERT INTO investigations (indicator, indicator_type, status)
        VALUES ($1, $2, 'pending')
        RETURNING id, indicator, indicator_type, status, created_at
        """,
        indicator,
        indicator_type,
    )
    set_investigation_id(str(row["id"]))
    log.info("investigation submitted", indicator=indicator, indicator_type=indicator_type)

    # If this publish fails the investigation stays 'pending' with no task in
    # flight. The stalled-investigation sweeper (Day 3) is what recovers it —
    # which is why the API does not try to roll back the insert here. A
    # recorded investigation that needs re-queuing beats a lost one.
    await tasks.publish(STREAM_ENRICH, str(row["id"]))

    return InvestigationCreated(**dict(row))


@router.get(
    "/{investigation_id}",
    response_model=InvestigationDetail,
    summary="Get status and result",
)
async def get_investigation(
    investigation_id: UUID,
    db: Database = Depends(get_db),
) -> InvestigationDetail:
    """Poll until status is ``complete`` or ``failed``."""
    set_investigation_id(str(investigation_id))

    row = await db.fetchrow(DETAIL_QUERY, investigation_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Investigation not found")

    result = None
    if row["classification"] is not None:
        result = AnalysisResult(
            classification=row["classification"],
            confidence=row["confidence"],
            explanation=row["explanation"],
            entity_ids=row["entity_ids"],
            evidence_refs=row["evidence_refs"],
            recommended_action=row["recommended_action"],
        )

    return InvestigationDetail(
        id=row["id"],
        indicator=row["indicator"],
        indicator_type=row["indicator_type"],
        status=row["status"],
        enrichment_status=row["enrichment_status"],
        error=row["error"],
        result=result,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.delete(
    "/{investigation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel an investigation",
)
async def cancel(
    investigation_id: UUID,
    db: Database = Depends(get_db),
) -> None:
    """Mark an investigation cancelled.

    Workers re-read the status at the start of each stage, so cancellation
    takes effect at the next stage boundary. Work already in flight finishes
    rather than being interrupted mid-write.
    """
    set_investigation_id(str(investigation_id))

    cancelled = await db.fetchval(
        """
        UPDATE investigations
           SET status = 'cancelled', updated_at = now()
         WHERE id = $1
           AND status NOT IN ('complete', 'failed', 'cancelled')
        RETURNING id
        """,
        investigation_id,
    )

    if cancelled is None:
        current = await db.fetchval(
            "SELECT status FROM investigations WHERE id = $1", investigation_id
        )
        if current is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Investigation not found")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Investigation is already {current} and cannot be cancelled",
        )

    log.info("investigation cancelled")
