"""The boundary between the agent loop and whatever is producing turns.

Two implementations satisfy this protocol: :mod:`analyst.anthropic_client`,
which calls Claude, and :mod:`analyst.stub_client`, which replays scripted
turns. The loop, the tools, the grounding check and the persistence path are
identical under both — which is the point. The scripted client exists so that
those can be tested deterministically and without cost, including failure
paths that are hard to provoke from a real model on demand.

What the stub cannot tell you is whether *Claude* behaves well: whether the
prompt works, whether it cites real evidence unprompted, whether it calls the
result tool exactly once. Those are properties of the model, and only the
live API tests them.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    """One assistant response.

    ``content`` is the raw block list, kept verbatim so it can be appended to
    the conversation unchanged. Rewriting it would break thinking-block replay
    on models that return them.
    """

    content: list[dict[str, Any]]
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None
    stop_reason: str = "end_turn"
    usage: dict[str, int] = field(default_factory=dict)


class ClaudeClient(Protocol):
    #: Shown in logs and in the result, so it is always clear which produced a verdict.
    name: str

    async def create_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn: ...

    async def close(self) -> None: ...
