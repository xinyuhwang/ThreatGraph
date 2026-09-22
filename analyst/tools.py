"""The analyst's tool surface: four reads and one write.

Five tools, deliberately. A small surface makes the agent's decisions easier
to audit and its behaviour more predictable than a large one would.

Note the asymmetry between reading and citing. The read tools can reach
observations from *any* investigation — that is how previously-seen
infrastructure becomes visible. But ``create_investigation_result`` may only
cite observations from the current one, because ``evidence_refs`` answers
"what did we collect that supports this verdict", not "what do we know in
general". Prior findings inform the reasoning and surface through
``entity_ids``; they are not evidence for today's conclusion.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from analyst.grounding import persist, verify
from analyst.schema import InvestigationResult, strict_tool_schema
from core.db import Database
from core.logging import get_logger

log = get_logger(__name__)

SEARCH_LIMIT = 20
RELATED_LIMIT = 25


@dataclass
class ToolOutcome:
    payload: Any
    is_error: bool = False
    #: Set when create_investigation_result succeeded; ends the agent loop.
    completed: bool = False
    result: InvestigationResult | None = field(default=None, repr=False)

    def to_text(self) -> str:
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, default=str)


def tool_definitions() -> list[dict[str, Any]]:
    """Definitions sent with every request.

    Byte-identical across investigations, so they sit in the cacheable prefix
    ahead of the system prompt.
    """
    return [
        {
            "name": "search_indicators",
            "description": (
                "Search previously recorded entities and investigations by value or "
                "keyword. Use this first to find out whether this indicator or its "
                "infrastructure has been seen before. Matches substrings, so 'paypal' "
                "finds 'paypal-secure-login.com'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Substring to search for in entity and indicator values.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "get_enrichment",
            "description": (
                "Retrieve enrichment observations for an entity. Returns everything "
                "recorded about that entity across all investigations, plus everything "
                "collected for the investigation you are working on — so a single call "
                "always shows you the complete citable evidence set, even when an "
                "investigation's observations are spread across several entities. "
                "Failed observations are included: a source that was attempted and "
                "produced nothing is different from one never run. Each result carries "
                "a `citable` flag; only citable observations may appear in evidence_refs."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "description": "Entity UUID."}
                },
                "required": ["entity_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "get_related_entities",
            "description": (
                "Traverse the relationship graph one hop from an entity. Returns "
                "connected domains, addresses and URLs with the relationship type and "
                "confidence. Use this to detect infrastructure shared with earlier "
                "investigations — for example, other domains resolving to the same "
                "address."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "description": "Entity UUID."}
                },
                "required": ["entity_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "inspect_observation",
            "description": (
                "Retrieve the complete raw payload of one observation. Use only when "
                "the summary from get_enrichment is not enough to decide — for example "
                "to read a full redirect chain or the complete DNS record set."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "observation_id": {"type": "string", "description": "Observation UUID."}
                },
                "required": ["observation_id"],
                "additionalProperties": False,
            },
            "strict": True,
        },
        {
            "name": "create_investigation_result",
            "description": (
                "Record your final conclusion. Call this exactly once, at the end, "
                "after gathering enough evidence. Every ID in evidence_refs must be an "
                "observation collected for the investigation you are working on; "
                "citations are verified against the database and an invalid one is "
                "rejected."
            ),
            "input_schema": strict_tool_schema(InvestigationResult),
            "strict": True,
        },
    ]


class ToolDispatcher:
    """Executes tool calls against the database for one investigation."""

    def __init__(self, db: Database, investigation_id: str) -> None:
        self.db = db
        self.investigation_id = investigation_id
        self.result_calls = 0

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        handler = {
            "search_indicators": self._search_indicators,
            "get_enrichment": self._get_enrichment,
            "get_related_entities": self._get_related_entities,
            "inspect_observation": self._inspect_observation,
            "create_investigation_result": self._create_result,
        }.get(name)

        if handler is None:
            return ToolOutcome(f"Unknown tool '{name}'.", is_error=True)

        try:
            return await handler(arguments)
        except Exception as exc:
            # Surfaced to the model as a tool error rather than killing the
            # loop, so it can adapt — a bad UUID is a recoverable mistake.
            log.exception("tool execution failed", tool=name)
            return ToolOutcome(f"{type(exc).__name__}: {exc}", is_error=True)

    async def _search_indicators(self, arguments: dict[str, Any]) -> ToolOutcome:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return ToolOutcome("query must not be empty.", is_error=True)

        entities = await self.db.fetch(
            """
            SELECT id, type, value, first_seen, last_seen
              FROM entities
             WHERE value ILIKE '%' || $1 || '%'
             ORDER BY similarity(value, $1) DESC
             LIMIT $2
            """,
            query,
            SEARCH_LIMIT,
        )
        investigations = await self.db.fetch(
            """
            SELECT i.id, i.indicator, i.indicator_type, i.status,
                   r.classification, r.confidence
              FROM investigations i
              LEFT JOIN investigation_results r ON r.investigation_id = i.id
             WHERE i.indicator ILIKE '%' || $1 || '%'
               AND i.id <> $2
             ORDER BY i.created_at DESC
             LIMIT $3
            """,
            query,
            self.investigation_id,
            SEARCH_LIMIT,
        )
        return ToolOutcome(
            {
                "entities": [dict(row) for row in entities],
                "past_investigations": [dict(row) for row in investigations],
            }
        )

    async def _get_enrichment(self, arguments: dict[str, Any]) -> ToolOutcome:
        rows = await self.db.fetch(
            """
            SELECT o.id, o.investigation_id, o.source, o.status, o.collected_at,
                   i.indicator
              FROM observations o
              JOIN investigations i ON i.id = o.investigation_id
             WHERE o.entity_id = $1::uuid
                OR o.investigation_id = $2
             ORDER BY o.collected_at DESC
             LIMIT 50
            """,
            arguments["entity_id"],
            self.investigation_id,
        )
        observations = []
        for row in rows:
            record = dict(row)
            record["citable"] = str(row["investigation_id"]) == str(self.investigation_id)
            observations.append(record)

        return ToolOutcome(
            {
                "observations": observations,
                "note": (
                    "Includes everything collected for the current investigation as "
                    "well as anything recorded about this entity previously. Only "
                    "observations with citable=true may appear in evidence_refs. Use "
                    "inspect_observation to read full payloads."
                ),
            }
        )

    async def _get_related_entities(self, arguments: dict[str, Any]) -> ToolOutcome:
        rows = await self.db.fetch(
            """
            SELECT r.relationship_type, r.confidence, r.observed_at,
                   other.id, other.type, other.value,
                   CASE WHEN r.source_entity_id = $1::uuid THEN 'outbound'
                        ELSE 'inbound' END AS direction
              FROM relationships r
              JOIN entities other
                ON other.id = CASE WHEN r.source_entity_id = $1::uuid
                                   THEN r.target_entity_id
                                   ELSE r.source_entity_id END
             WHERE r.source_entity_id = $1::uuid OR r.target_entity_id = $1::uuid
             ORDER BY r.confidence DESC, other.value
             LIMIT $2
            """,
            arguments["entity_id"],
            RELATED_LIMIT,
        )
        return ToolOutcome({"related": [dict(row) for row in rows]})

    async def _inspect_observation(self, arguments: dict[str, Any]) -> ToolOutcome:
        row = await self.db.fetchrow(
            """
            SELECT id, investigation_id, source, status, data, collected_at
              FROM observations
             WHERE id = $1::uuid
            """,
            arguments["observation_id"],
        )
        if row is None:
            return ToolOutcome(
                f"No observation with id {arguments['observation_id']}.", is_error=True
            )

        record = dict(row)
        record["citable"] = str(row["investigation_id"]) == str(self.investigation_id)
        return ToolOutcome(record)

    async def _create_result(self, arguments: dict[str, Any]) -> ToolOutcome:
        self.result_calls += 1
        if self.result_calls > 1:
            return ToolOutcome(
                "create_investigation_result has already been called for this "
                "investigation. Do not call it again.",
                is_error=True,
            )

        try:
            result = InvestigationResult(**arguments)
        except Exception as exc:
            self.result_calls -= 1  # a rejected attempt does not count
            return ToolOutcome(f"Result failed validation: {exc}", is_error=True)

        failure = await verify(self.db, self.investigation_id, result)
        if failure is not None:
            self.result_calls -= 1  # let the model correct itself
            return ToolOutcome(failure.message(), is_error=True)

        await persist(self.db, self.investigation_id, result)
        return ToolOutcome(
            {"status": "recorded", "classification": result.classification},
            completed=True,
            result=result,
        )
