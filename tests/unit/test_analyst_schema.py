import uuid

import pytest
from pydantic import ValidationError

from analyst.schema import InvestigationResult, strict_tool_schema


def valid_payload(**overrides):
    payload = {
        "classification": "suspicious",
        "confidence": 0.7,
        "explanation": "A sufficiently long explanation of the finding.",
        "entity_ids": [str(uuid.uuid4())],
        "evidence_refs": [str(uuid.uuid4())],
        "recommended_action": "monitor",
    }
    payload.update(overrides)
    return payload


def test_accepts_a_well_formed_result():
    assert InvestigationResult(**valid_payload()).classification == "suspicious"


@pytest.mark.parametrize(
    "overrides",
    [
        {"classification": "very-bad"},
        {"recommended_action": "delete-the-internet"},
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"explanation": "too short"},
        {"explanation": "x" * 1001},
        {"evidence_refs": []},
        {"entity_ids": []},
        {"evidence_refs": ["not-a-uuid"]},
    ],
)
def test_rejects_malformed_results(overrides):
    with pytest.raises(ValidationError):
        InvestigationResult(**valid_payload(**overrides))


def test_duplicate_citations_are_collapsed():
    """Citing one observation twice is not two pieces of evidence."""
    ref = str(uuid.uuid4())
    result = InvestigationResult(**valid_payload(evidence_refs=[ref, ref, ref]))
    assert len(result.evidence_refs) == 1


def test_schema_validation_cannot_detect_an_invented_uuid():
    """The gap that makes the database check necessary.

    A UUID the model made up is indistinguishable, to any schema, from one it
    read out of a tool result. Only a lookup can tell them apart.
    """
    invented = "deadbeef-0000-4000-8000-000000000000"
    result = InvestigationResult(**valid_payload(evidence_refs=[invented]))
    assert str(result.evidence_refs[0]) == invented


def test_tool_schema_meets_strict_mode_requirements():
    schema = strict_tool_schema(InvestigationResult)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_tool_schema_exposes_every_field():
    schema = strict_tool_schema(InvestigationResult)
    assert set(schema["properties"]) == {
        "classification",
        "confidence",
        "explanation",
        "entity_ids",
        "evidence_refs",
        "recommended_action",
    }
