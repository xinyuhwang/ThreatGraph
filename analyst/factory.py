"""Choose which client the analyst runs against."""

from analyst.client import ClaudeClient
from analyst.stub_client import ScriptedClient
from core.config import settings
from core.logging import get_logger

log = get_logger(__name__)


def resolve_mode() -> str:
    """Decide between the live API and the scripted client.

    ``auto`` picks Claude when a key is configured and the scripted client
    otherwise, so a fresh clone runs end to end without one.
    """
    mode = settings.analyst_client.lower()
    if mode != "auto":
        return mode
    return "anthropic" if settings.anthropic_api_key.strip() else "stub"


def build_client(indicator: str, primary_entity_id: str | None) -> ClaudeClient:
    mode = resolve_mode()

    if mode == "anthropic":
        # Imported lazily so the scripted path does not require the SDK to be
        # importable or a key to be present.
        from analyst.anthropic_client import AnthropicClient

        log.info("analyst using the live API", model=settings.anthropic_model)
        return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model)

    log.warning(
        "analyst using the scripted client — conclusions are not model-generated "
        "and are prefixed [stub]. Set ANTHROPIC_API_KEY to use Claude.",
    )
    return ScriptedClient.evidence_following(indicator, primary_entity_id or "")
