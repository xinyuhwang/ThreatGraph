"""Request and response models.

Note what is absent from :class:`SubmitInvestigation`: ``indicator_type``. The
server derives it, so a client cannot mislabel a URL as a domain and send it
down the wrong enrichment path.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SubmitInvestigation(BaseModel):
    indicator: str = Field(
        min_length=1,
        max_length=2048,
        description="A domain (example.com) or URL (https://example.com/path).",
        examples=["suspicious-domain.com"],
    )


class InvestigationCreated(BaseModel):
    id: UUID
    indicator: str
    indicator_type: str
    status: str
    created_at: datetime


class AnalysisResult(BaseModel):
    classification: str
    confidence: float
    explanation: str
    entity_ids: list[UUID]
    #: Observation IDs supporting this conclusion. Every one is verified to
    #: exist and belong to this investigation before the result is stored.
    evidence_refs: list[UUID]
    recommended_action: str


class InvestigationDetail(BaseModel):
    id: UUID
    indicator: str
    indicator_type: str
    status: str
    #: Per-source enrichment outcome, e.g. {"dns": "ok", "http": "failed"}.
    enrichment_status: dict[str, str]
    error: str | None = None
    result: AnalysisResult | None = None
    created_at: datetime
    updated_at: datetime


class DependencyHealth(BaseModel):
    status: str
    dependencies: dict[str, str]


class ErrorResponse(BaseModel):
    detail: str
    code: str
    request_id: str | None = None
    investigation_id: UUID | None = None
