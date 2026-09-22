"""The analyst's output contract.

This model is the *shape* check. It cannot be the whole story: ``list[UUID]``
accepts any well-formed UUID, including one the model invented. Verifying that
those UUIDs name real observations belonging to this investigation happens in
:mod:`analyst.grounding`, against the database.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Classification = Literal["benign", "suspicious", "malicious", "unknown"]
RecommendedAction = Literal["monitor", "block", "escalate", "no_action", "request_review"]


class InvestigationResult(BaseModel):
    classification: Classification
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=20, max_length=1000)
    entity_ids: list[UUID] = Field(min_length=1)
    evidence_refs: list[UUID] = Field(min_length=1)
    recommended_action: RecommendedAction

    @field_validator("entity_ids", "evidence_refs")
    @classmethod
    def deduplicate(cls, value: list[UUID]) -> list[UUID]:
        """Citing the same observation twice is not two pieces of evidence."""
        seen: list[UUID] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        return seen


def strict_tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Turn a Pydantic model into a JSON schema usable with ``strict: true``.

    Strict tool use guarantees the arguments Claude sends validate against
    this schema, which means malformed input is rejected by the API rather
    than arriving here for Pydantic to catch. Two things it requires:
    ``additionalProperties: false``, and every property listed in ``required``.
    """
    schema = model.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = sorted(schema.get("properties", {}))
    schema.pop("title", None)
    return schema
