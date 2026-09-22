"""A scripted stand-in for Claude.

Lets the whole analyst path — tools, grounding, persistence, the agent loop —
be built and tested without an API key and without cost. The default scenario
does not fabricate output: it reads the tool results it is given and cites the
observation IDs that actually came back, the way a real model would.

Conclusions produced this way are prefixed ``[stub]``. That is deliberate. A
reviewer running this project should never mistake a scripted verdict for one
Claude produced, and the marker disappears the moment a real key is
configured.

The scenarios beyond the default exist to provoke failure paths on demand:
citing a hallucinated UUID, refusing, or never concluding. Those are awkward
to elicit from a live model and are exactly what the grounding check and the
iteration cap are for.
"""

import json
from collections.abc import Callable
from typing import Any

from analyst.client import AssistantTurn, ToolCall
from core.logging import get_logger

log = get_logger(__name__)

STUB_PREFIX = "[stub]"

Responder = Callable[[list[dict[str, Any]]], AssistantTurn]

HALLUCINATED_UUID = "deadbeef-0000-4000-8000-000000000000"


def _tool_results(messages: list[dict[str, Any]]) -> list[Any]:
    """Every tool result so far, JSON-decoded where possible."""
    payloads: list[Any] = []
    for message in messages:
        if message.get("role") != "user" or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            try:
                payloads.append(json.loads(block.get("content", "")))
            except (TypeError, ValueError):
                payloads.append(block.get("content"))
    return payloads


def _citable_observations(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Observations the tools reported as belonging to this investigation."""
    found: dict[str, dict[str, Any]] = {}
    for payload in _tool_results(messages):
        if not isinstance(payload, dict):
            continue
        for observation in payload.get("observations", []):
            if observation.get("citable"):
                found[observation["id"]] = observation
    return list(found.values())


def _inspected_payloads(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Full observation records returned by inspect_observation."""
    return [
        payload
        for payload in _tool_results(messages)
        if isinstance(payload, dict) and "data" in payload and "source" in payload
    ]


def _tool_use(name: str, arguments: dict[str, Any], index: int) -> AssistantTurn:
    call_id = f"toolu_stub_{index}"
    return AssistantTurn(
        content=[{"type": "tool_use", "id": call_id, "name": name, "input": arguments}],
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        stop_reason="tool_use",
    )


def _text(message: str) -> AssistantTurn:
    return AssistantTurn(
        content=[{"type": "text", "text": message}], text=message, stop_reason="end_turn"
    )


def _summarise(observations: list[dict[str, Any]], inspected: list[dict[str, Any]]) -> tuple:
    """Derive a defensible verdict from the evidence actually returned.

    Not an imitation of judgement — just the mechanical reading. A failed or
    non-existent source yields `unknown`, because that is what the system
    prompt tells a real model to do in the same situation.
    """
    sources = sorted({obs["source"] for obs in observations})
    failed = sorted({obs["source"] for obs in observations if obs.get("status") == "failed"})

    nxdomain = any(payload.get("data", {}).get("nxdomain") for payload in inspected)
    resolved: list[str] = []
    for payload in inspected:
        resolved.extend(payload.get("data", {}).get("resolved_ips", []))

    if nxdomain:
        return (
            "unknown",
            0.3,
            "DNS enrichment reports that this domain does not resolve. With no "
            "hosting infrastructure to examine and no HTTP response to inspect, "
            "there is not enough evidence to classify it either way.",
            "request_review",
        )
    if failed:
        return (
            "unknown",
            0.35,
            f"Enrichment was incomplete: {', '.join(failed)} was attempted and "
            f"produced no data, leaving only {', '.join(sources)} to reason from. "
            "That is not sufficient to reach a confident classification.",
            "request_review",
        )

    address_note = (
        f"It resolves to {len(resolved)} address(es)." if resolved else "No addresses recorded."
    )
    return (
        "benign",
        0.6,
        f"Evidence was collected from {', '.join(sources)} with no failures. "
        f"{address_note} Nothing in the collected records indicates abuse: the "
        "response and DNS configuration are consistent with ordinary operation.",
        "monitor",
    )


class ScriptedClient:
    """Replays a fixed sequence of responders."""

    name = "stub"

    def __init__(self, responders: list[Responder]) -> None:
        self._responders = responders
        self._turn = 0

    async def create_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        index = min(self._turn, len(self._responders) - 1)
        self._turn += 1
        return self._responders[index](messages)

    async def close(self) -> None:
        return None

    # -- scenarios ---------------------------------------------------------

    @classmethod
    def evidence_following(cls, indicator: str, primary_entity_id: str) -> "ScriptedClient":
        """The default. Reads the evidence and cites what it actually found."""

        def conclude(messages: list[dict[str, Any]]) -> AssistantTurn:
            observations = _citable_observations(messages)
            if not observations:
                return _text(
                    f"{STUB_PREFIX} No citable observations were returned, so no "
                    "grounded conclusion is possible."
                )

            classification, confidence, explanation, action = _summarise(
                observations, _inspected_payloads(messages)
            )
            return _tool_use(
                "create_investigation_result",
                {
                    "classification": classification,
                    "confidence": confidence,
                    "explanation": f"{STUB_PREFIX} {explanation}",
                    "entity_ids": [primary_entity_id],
                    "evidence_refs": [obs["id"] for obs in observations],
                    "recommended_action": action,
                },
                index=5,
            )

        def inspect_first(messages: list[dict[str, Any]]) -> AssistantTurn:
            observations = _citable_observations(messages)
            dns = next((o for o in observations if o["source"] == "dns"), None)
            target = dns or (observations[0] if observations else None)
            if target is None:
                return conclude(messages)
            return _tool_use("inspect_observation", {"observation_id": target["id"]}, index=3)

        return cls(
            [
                lambda _m: _tool_use("search_indicators", {"query": indicator}, index=1),
                lambda _m: _tool_use("get_enrichment", {"entity_id": primary_entity_id}, 2),
                inspect_first,
                lambda _m: _tool_use("get_related_entities", {"entity_id": primary_entity_id}, 4),
                conclude,
            ]
        )

    @classmethod
    def hallucinating(cls, primary_entity_id: str) -> "ScriptedClient":
        """Cites an invented observation first, then corrects itself.

        Exercises the path the grounding check exists for: a well-formed UUID
        that no schema can reject, refused by the database lookup and fixed on
        the retry.
        """
        base = {
            "classification": "suspicious",
            "confidence": 0.7,
            "explanation": (
                f"{STUB_PREFIX} Scripted scenario exercising the grounding "
                "rejection path with an invented evidence reference."
            ),
            "entity_ids": [primary_entity_id],
            "recommended_action": "monitor",
        }

        def invented(_messages: list[dict[str, Any]]) -> AssistantTurn:
            return _tool_use(
                "create_investigation_result",
                {**base, "evidence_refs": [HALLUCINATED_UUID]},
                index=1,
            )

        def corrected(messages: list[dict[str, Any]]) -> AssistantTurn:
            observations = _citable_observations(messages)
            return _tool_use(
                "create_investigation_result",
                {**base, "evidence_refs": [obs["id"] for obs in observations]},
                index=3,
            )

        return cls(
            [
                lambda _m: _tool_use("get_enrichment", {"entity_id": primary_entity_id}, 0),
                invented,
                corrected,
            ]
        )

    @classmethod
    def refusing(cls) -> "ScriptedClient":
        """Returns a refusal, as the safety classifier may for this domain."""
        return cls([lambda _m: AssistantTurn(content=[], stop_reason="refusal")])

    @classmethod
    def never_concluding(cls, primary_entity_id: str) -> "ScriptedClient":
        """Loops on a read tool forever, to prove the iteration cap holds."""
        return cls(
            [lambda _m: _tool_use("get_enrichment", {"entity_id": primary_entity_id}, 0)]
        )
