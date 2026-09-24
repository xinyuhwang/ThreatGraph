"""The grounding check, against a real database.

These are the tests that justify the platform's central claim. Everything
else is plumbing; this is the part that decides whether an AI conclusion can
be trusted.
"""

import uuid

import pytest

from analyst.agent import AnalysisFailed, analyse
from analyst.grounding import persist, verify
from analyst.schema import InvestigationResult
from analyst.stub_client import HALLUCINATED_UUID, ScriptedClient
from analyst.tools import ToolDispatcher

pytestmark = pytest.mark.integration


def result_citing(evidence_refs, entity_ids, **overrides):
    payload = {
        "classification": "suspicious",
        "confidence": 0.7,
        "explanation": "An explanation long enough to satisfy the schema constraint.",
        "entity_ids": entity_ids,
        "evidence_refs": evidence_refs,
        "recommended_action": "monitor",
    }
    payload.update(overrides)
    return InvestigationResult(**payload)


async def test_real_citations_pass(db, investigation):
    result = result_citing(sorted(investigation["observation_ids"]), [investigation["entity_id"]])
    assert await verify(db, investigation["id"], result) is None


async def test_invented_uuid_is_rejected(db, investigation):
    """The failure no schema can catch: a well-formed UUID that names nothing."""
    result = result_citing([HALLUCINATED_UUID], [investigation["entity_id"]])

    failure = await verify(db, investigation["id"], result)

    assert failure is not None
    assert [str(u) for u in failure.unknown_evidence] == [HALLUCINATED_UUID]
    assert "do not exist" in failure.message()


async def test_evidence_from_another_investigation_is_rejected(
    db, investigation, other_investigation
):
    """A real observation is still not evidence for *this* verdict."""
    result = result_citing([other_investigation["observation_id"]], [investigation["entity_id"]])

    failure = await verify(db, investigation["id"], result)

    assert failure is not None
    assert not failure.unknown_evidence
    assert [str(u) for u in failure.foreign_evidence] == [other_investigation["observation_id"]]


async def test_invented_entity_is_rejected(db, investigation):
    invented = str(uuid.uuid4())
    result = result_citing(sorted(investigation["observation_ids"]), [invented])

    failure = await verify(db, investigation["id"], result)

    assert failure is not None
    assert [str(u) for u in failure.unknown_entities] == [invented]


async def test_tool_refuses_to_persist_an_ungrounded_result(db, investigation):
    """End to end through the tool the model actually calls."""
    dispatcher = ToolDispatcher(db, investigation["id"])

    outcome = await dispatcher.dispatch(
        "create_investigation_result",
        {
            "classification": "malicious",
            "confidence": 0.9,
            "explanation": "A confident claim resting on evidence that does not exist.",
            "entity_ids": [investigation["entity_id"]],
            "evidence_refs": [HALLUCINATED_UUID],
            "recommended_action": "block",
        },
    )

    assert outcome.is_error
    assert not outcome.completed
    stored = await db.fetchval(
        "SELECT count(*) FROM investigation_results WHERE investigation_id = $1",
        investigation["id"],
    )
    assert stored == 0, "an ungrounded result reached the database"


async def test_agent_recovers_after_a_rejected_citation(db, investigation):
    """The full loop: hallucinate, get refused, cite real evidence, succeed."""
    client = ScriptedClient.hallucinating(investigation["entity_id"])

    outcome = await analyse(db, investigation["id"], client)

    assert outcome.iterations == 3
    assert {str(ref) for ref in outcome.result.evidence_refs} == investigation["observation_ids"]

    linked = await db.fetch(
        """
        SELECT re.observation_id
          FROM result_evidence re
          JOIN investigation_results r ON r.id = re.result_id
         WHERE r.investigation_id = $1
        """,
        investigation["id"],
    )
    assert {str(row["observation_id"]) for row in linked} == investigation["observation_ids"]


async def test_evidence_following_client_produces_a_grounded_result(db, investigation):
    client = ScriptedClient.evidence_following("grounding-test.example", investigation["entity_id"])

    outcome = await analyse(db, investigation["id"], client)

    assert outcome.result.evidence_refs
    assert {str(ref) for ref in outcome.result.evidence_refs} <= investigation["observation_ids"]
    assert outcome.result.explanation.startswith("[stub]")


async def test_iteration_cap_stops_a_model_that_never_concludes(db, investigation):
    client = ScriptedClient.never_concluding(investigation["entity_id"])

    with pytest.raises(AnalysisFailed, match="iteration cap"):
        await analyse(db, investigation["id"], client)


async def test_refusal_does_not_become_a_guess(db, investigation):
    """A declined request must fail loudly, not fall back to a fabricated verdict."""
    with pytest.raises(AnalysisFailed, match="declined"):
        await analyse(db, investigation["id"], ScriptedClient.refusing())

    stored = await db.fetchval(
        "SELECT count(*) FROM investigation_results WHERE investigation_id = $1",
        investigation["id"],
    )
    assert stored == 0


async def test_persisting_twice_does_not_duplicate(db, investigation):
    """A redelivered analyst task must not write a second conclusion."""
    result = result_citing(sorted(investigation["observation_ids"]), [investigation["entity_id"]])

    first = await persist(db, investigation["id"], result)
    second = await persist(db, investigation["id"], result)

    assert first == second
    count = await db.fetchval(
        "SELECT count(*) FROM investigation_results WHERE investigation_id = $1",
        investigation["id"],
    )
    assert count == 1


async def test_result_tool_can_only_be_called_once(db, investigation):
    dispatcher = ToolDispatcher(db, investigation["id"])
    arguments = {
        "classification": "benign",
        "confidence": 0.5,
        "explanation": "A grounded conclusion citing real collected evidence.",
        "entity_ids": [investigation["entity_id"]],
        "evidence_refs": sorted(investigation["observation_ids"]),
        "recommended_action": "monitor",
    }

    first = await dispatcher.dispatch("create_investigation_result", arguments)
    second = await dispatcher.dispatch("create_investigation_result", arguments)

    assert first.completed
    assert second.is_error
    assert "already been called" in second.payload
