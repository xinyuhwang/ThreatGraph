"""Environment-driven settings, resolved once at import."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://threatgraph:threatgraph@postgres:5432/threatgraph"
    redis_url: str = "redis://redis:6379/0"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"

    log_level: str = "INFO"

    # Bounds on the agent loop. Without these, a looping model occupies a
    # worker indefinitely and silently doubles the API spend.
    analyst_max_iterations: int = 10
    analyst_timeout_seconds: int = 180

    # Reclaim thresholds differ per stage on purpose. Enrichment finishes in
    # seconds; an agentic Claude loop making several tool calls routinely runs
    # past thirty, so a shared threshold would keep reclaiming healthy work.
    reclaim_idle_enrich_ms: int = 30_000
    reclaim_idle_analyze_ms: int = 300_000

    rate_limit_per_minute: int = 30
    seed_demo_data: bool = True

    # How long a worker blocks on XREADGROUP before looping. Keeps shutdown
    # responsive without busy-waiting.
    worker_block_ms: int = 5_000


settings = Settings()
