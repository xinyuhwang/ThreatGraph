"""Verify that an AI conclusion actually rests on collected evidence.

Three layers protect the result, each catching what the previous cannot:

===========================  ========================================
``strict: true`` on the tool  malformed arguments, wrong types
Pydantic model                value ranges, enum membership, length
**this module**               **invented or foreign evidence IDs**
===========================  ========================================

The third layer is the one that matters. Schema validation confirms the shape
of an answer; only a database lookup confirms that the observations it cites
are real and belong to this investigation. A model can emit a perfectly
well-formed UUID it made up.

The ``result_evidence`` foreign key is the final backstop: even if every check
above were bypassed, an invalid reference cannot be written.
"""

from dataclasses import dataclass
from uuid import UUID

from analyst.schema import InvestigationResult
from core.db import Database
from core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class GroundingFailure:
    """Why a result was refused, phrased so the model can act on it."""

    unknown_evidence: list[UUID]
    foreign_evidence: list[UUID]
    unknown_entities: list[UUID]

    def message(self) -> str:
        parts: list[str] = []
        if self.unknown_evidence:
            parts.append(
                "These evidence_refs do not exist: "
                f"{[str(u) for u in self.unknown_evidence]}."
            )
        if self.foreign_evidence:
            parts.append(
                "These evidence_refs belong to a different investigation: "
                f"{[str(u) for u in self.foreign_evidence]}. You may read other "
                "investigations for context, but you may only cite observations "
                "collected for this one."
            )
        if self.unknown_entities:
            parts.append(
                f"These entity_ids do not exist: {[str(u) for u in self.unknown_entities]}."
            )
        parts.append(
            "Re-issue create_investigation_result using only IDs that a tool returned to you."
        )
        return " ".join(parts)


async def verify(
    db: Database, investigation_id: str, result: InvestigationResult
) -> GroundingFailure | None:
    """Check the result's citations against the database.

    Returns None when the result is grounded, or a :class:`GroundingFailure`
    describing precisely what was wrong.
    """
    rows = await db.fetch(
        """
        SELECT id, investigation_id
          FROM observations
         WHERE id = ANY($1::uuid[])
        """,
        result.evidence_refs,
    )
    found = {row["id"]: row["investigation_id"] for row in rows}

    unknown_evidence = [ref for ref in result.evidence_refs if ref not in found]
    foreign_evidence = [
        ref
        for ref in result.evidence_refs
        if ref in found and str(found[ref]) != str(investigation_id)
    ]

    known_entities = {
        row["id"]
        for row in await db.fetch(
            "SELECT id FROM entities WHERE id = ANY($1::uuid[])", result.entity_ids
        )
    }
    unknown_entities = [eid for eid in result.entity_ids if eid not in known_entities]

    if not (unknown_evidence or foreign_evidence or unknown_entities):
        return None

    failure = GroundingFailure(
        unknown_evidence=unknown_evidence,
        foreign_evidence=foreign_evidence,
        unknown_entities=unknown_entities,
    )
    log.warning(
        "grounding check rejected a result",
        unknown_evidence=len(unknown_evidence),
        foreign_evidence=len(foreign_evidence),
        unknown_entities=len(unknown_entities),
    )
    return failure


async def persist(db: Database, investigation_id: str, result: InvestigationResult) -> UUID:
    """Store a verified result and its evidence links in one transaction.

    The result row and its citations are written together: a result that
    exists without its evidence links would be exactly the ungrounded artifact
    this whole mechanism is meant to prevent.
    """
    async with db.transaction() as conn:
        result_id = await conn.fetchval(
            """
            INSERT INTO investigation_results
                (investigation_id, classification, confidence, explanation,
                 entity_ids, recommended_action)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (investigation_id) DO NOTHING
            RETURNING id
            """,
            investigation_id,
            result.classification,
            result.confidence,
            result.explanation,
            [str(eid) for eid in result.entity_ids],
            result.recommended_action,
        )

        if result_id is None:
            # A result already exists. Another worker analysed this
            # investigation — a duplicate delivery, not an error.
            existing = await conn.fetchval(
                "SELECT id FROM investigation_results WHERE investigation_id = $1",
                investigation_id,
            )
            log.info("result already recorded, leaving it untouched")
            return existing

        await conn.executemany(
            "INSERT INTO result_evidence (result_id, observation_id) VALUES ($1, $2)",
            [(result_id, ref) for ref in result.evidence_refs],
        )

    log.info(
        "result persisted",
        classification=result.classification,
        confidence=result.confidence,
        evidence_count=len(result.evidence_refs),
    )
    return result_id
