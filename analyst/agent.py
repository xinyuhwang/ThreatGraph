"""The agent loop.

Identical under both clients. Everything that makes the analyst trustworthy —
the iteration cap, the grounding check, the refusal path, the requirement that
a result is actually recorded — lives here rather than in either client.
"""

from dataclasses import dataclass
from typing import Any

from analyst.client import ClaudeClient
from analyst.prompts import SYSTEM_PROMPT, build_evidence_packet
from analyst.schema import InvestigationResult
from analyst.tools import ToolDispatcher, tool_definitions
from core.config import settings
from core.db import Database
from core.logging import get_logger

log = get_logger(__name__)


class AnalysisFailed(Exception):
    """The analyst finished without recording a grounded result."""


@dataclass
class AnalysisOutcome:
    result: InvestigationResult
    iterations: int
    client_name: str


async def load_evidence(db: Database, investigation_id: str) -> dict[str, Any]:
    investigation = await db.fetchrow(
        """
        SELECT indicator, indicator_type, enrichment_status
          FROM investigations WHERE id = $1
        """,
        investigation_id,
    )
    observations = await db.fetch(
        """
        SELECT id, source, status, entity_id
          FROM observations WHERE investigation_id = $1 ORDER BY source
        """,
        investigation_id,
    )
    primary = await db.fetchrow(
        """
        SELECT e.id, e.type, e.value
          FROM entities e
          JOIN observations o ON o.entity_id = e.id
         WHERE o.investigation_id = $1
         ORDER BY CASE e.type WHEN 'url' THEN 0 WHEN 'domain' THEN 1 ELSE 2 END
         LIMIT 1
        """,
        investigation_id,
    )
    return {
        "indicator": investigation["indicator"],
        "indicator_type": investigation["indicator_type"],
        "enrichment_status": investigation["enrichment_status"] or {},
        "observations": [dict(row) for row in observations],
        "primary_entity": dict(primary) if primary else None,
    }


async def analyse(
    db: Database,
    investigation_id: str,
    client: ClaudeClient,
) -> AnalysisOutcome:
    """Run the agent until it records a verified result, or give up."""
    evidence = await load_evidence(db, investigation_id)
    dispatcher = ToolDispatcher(db, investigation_id)
    tools = tool_definitions()

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": build_evidence_packet(
                indicator=evidence["indicator"],
                indicator_type=evidence["indicator_type"],
                enrichment_status=evidence["enrichment_status"],
                observations=evidence["observations"],
                primary_entity=evidence["primary_entity"],
            ),
        }
    ]

    for iteration in range(1, settings.analyst_max_iterations + 1):
        turn = await client.create_turn(system=SYSTEM_PROMPT, messages=messages, tools=tools)

        if turn.stop_reason == "refusal":
            raise AnalysisFailed(
                "The model declined to analyse this indicator. Recorded as unreviewed "
                "rather than guessed at."
            )

        # Appended verbatim. Rewriting the blocks would break thinking-block
        # replay on models that return them.
        messages.append({"role": "assistant", "content": turn.content})

        if not turn.tool_calls:
            raise AnalysisFailed(
                "The model ended its turn without calling create_investigation_result."
            )

        tool_results: list[dict[str, Any]] = []
        outcome = None
        for call in turn.tool_calls:
            log.debug("tool call", tool=call.name, iteration=iteration)
            outcome = await dispatcher.dispatch(call.name, call.arguments)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": outcome.to_text(),
                    "is_error": outcome.is_error,
                }
            )
            if outcome.completed:
                log.info(
                    "analysis complete",
                    iterations=iteration,
                    client=client.name,
                    classification=outcome.result.classification,
                )
                return AnalysisOutcome(
                    result=outcome.result, iterations=iteration, client_name=client.name
                )

        # All results for one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": tool_results})

    raise AnalysisFailed(
        f"Reached the {settings.analyst_max_iterations}-iteration cap without a recorded result."
    )
