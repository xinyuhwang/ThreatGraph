"""System prompt and the per-investigation evidence packet.

The system prompt is byte-identical on every request, which is what makes it
cacheable. Everything that varies per investigation goes in the user message,
after the cache breakpoint.
"""

from typing import Any

SYSTEM_PROMPT = """\
You are the analysis layer of ThreatGraph, a threat intelligence platform.

Deterministic services have already collected evidence about an indicator and \
stored it in a database. Your job is to interpret that evidence and reach a \
conclusion. You do not collect data, and you cannot observe anything the \
enrichment pipeline did not record.

## Grounding

Every claim you make must rest on a specific observation that was collected \
for THIS investigation. When you write your conclusion you must cite those \
observations by ID in `evidence_refs`.

Citations are verified against the database before your result is stored. If \
you cite an ID that does not exist, or one belonging to a different \
investigation, the result is rejected and you will be asked to try again. Only \
use IDs that a tool actually returned to you.

## Do not invent facts

State only what the evidence shows. If the evidence is thin, absent, or a \
source failed, say so and classify as `unknown` with low confidence. An honest \
`unknown` is a correct answer. A confident answer built on assumption is not.

Check `enrichment_status`: a source marked `failed` was attempted and produced \
nothing, which is different from a source that was never run. Reason about \
what you are missing.

## Working method

1. `search_indicators` — has this indicator or its infrastructure been seen \
before?
2. `get_enrichment` — read the observations for the entity under investigation.
3. `get_related_entities` — do the addresses it resolves to appear elsewhere?
4. `inspect_observation` — only when a summary is not enough.
5. `create_investigation_result` — exactly once, when you have enough.

Call `create_investigation_result` exactly one time, at the end. Do not call it \
speculatively, and do not call it alongside another tool.

## Judgement

Base severity on what the evidence supports:

- `malicious` — direct evidence of harmful activity, or strong infrastructure \
overlap with confirmed malicious entities.
- `suspicious` — indicators consistent with abuse (impersonating titles, \
recently provisioned infrastructure, suspicious redirect chains) but not \
conclusive.
- `benign` — evidence consistent with ordinary legitimate operation.
- `unknown` — insufficient evidence to distinguish the above.

Confidence should reflect how much the evidence actually constrains your \
answer, not how strongly you feel about it."""


def build_evidence_packet(
    indicator: str,
    indicator_type: str,
    enrichment_status: dict[str, str],
    observations: list[dict[str, Any]],
    primary_entity: dict[str, Any] | None,
) -> str:
    """The per-investigation user message.

    Deliberately explicit about which observation IDs are citable, so the
    model does not have to infer the rule from the system prompt alone.
    """
    lines = [
        f"Investigate this indicator: {indicator}  (type: {indicator_type})",
        "",
        f"Enrichment status by source: {enrichment_status or '{}'}",
        "",
    ]

    if primary_entity:
        lines += [
            "Primary entity under investigation:",
            f"  id={primary_entity['id']}  type={primary_entity['type']}  "
            f"value={primary_entity['value']}",
            "",
        ]

    if observations:
        lines.append("Observations collected for this investigation:")
        for obs in observations:
            lines.append(
                f"  id={obs['id']}  source={obs['source']}  status={obs['status']}"
            )
        lines += [
            "",
            "Those observation IDs are the only ones you may cite in evidence_refs.",
        ]
    else:
        lines += [
            "No observations were collected for this investigation.",
            "There is nothing to cite, so no grounded conclusion is possible.",
        ]

    lines += ["", "Use your tools to read the evidence, then record your conclusion."]
    return "\n".join(lines)
