"""Claude-backed implementation of :class:`~analyst.client.ClaudeClient`.

Untested against the live API at time of writing — the project was built
without a key, using the scripted client. The request shape follows the
documented Messages API; treat the first live run as the real test.
"""

from typing import Any

from anthropic import AsyncAnthropic

from analyst.client import AssistantTurn, ToolCall
from core.config import settings
from core.logging import get_logger

log = get_logger(__name__)

MAX_TOKENS = 16_000


class AnthropicClient:
    name = "anthropic"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key, timeout=settings.analyst_timeout_seconds)
        self._model = model

    async def create_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            # Requests render as tools -> system -> messages. Both are
            # identical on every investigation, so one breakpoint at the end
            # of the system block caches the whole stable prefix; only the
            # evidence that follows it varies.
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.analyst_effort},
            # The result tool must not arrive alongside another call.
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        )

        # Checked before reading content: a refusal returns HTTP 200 with a
        # stop_reason of "refusal" and nothing usable in the body. An analyst
        # reasoning about malicious infrastructure is a plausible trigger, so
        # this is a path that will actually be taken.
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            log.warning("claude declined the request", category=category)
            return AssistantTurn(content=[], stop_reason="refusal")

        content = [block.model_dump() for block in response.content]

        tool_calls = [
            ToolCall(id=block["id"], name=block["name"], arguments=block["input"])
            for block in content
            if block.get("type") == "tool_use"
        ]
        text = "\n".join(
            block["text"] for block in content if block.get("type") == "text"
        ).strip()

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
            "cache_creation_input_tokens": getattr(
                response.usage, "cache_creation_input_tokens", 0
            ),
        }
        # A cache read of zero across consecutive investigations means
        # something volatile leaked into the prefix.
        log.debug("claude turn", stop_reason=response.stop_reason, **usage)

        return AssistantTurn(
            content=content,
            tool_calls=tool_calls,
            text=text or None,
            stop_reason=response.stop_reason or "end_turn",
            usage=usage,
        )

    async def close(self) -> None:
        await self._client.close()
