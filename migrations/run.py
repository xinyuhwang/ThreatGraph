"""Apply pending SQL migrations, in filename order, exactly once each.

Runs as a one-shot Compose service that the API and workers wait on, so no two
services can race to apply the same file.
"""

import asyncio
import pathlib
import sys

import asyncpg

from core.config import settings
from core.logging import configure_logging, get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = pathlib.Path(__file__).parent

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


async def connect_with_retry(dsn: str, attempts: int = 30) -> asyncpg.Connection:
    """Postgres accepts connections slightly after it reports healthy."""
    for attempt in range(1, attempts + 1):
        try:
            return await asyncpg.connect(dsn)
        except (OSError, asyncpg.PostgresError) as exc:
            if attempt == attempts:
                raise
            log.warning("postgres not ready, retrying", error=str(exc), attempt=attempt)
            await asyncio.sleep(1)
    raise RuntimeError("unreachable")


async def main() -> int:
    configure_logging()
    conn = await connect_with_retry(settings.database_url)
    try:
        await conn.execute(BOOTSTRAP)
        applied = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM schema_migrations")
        }

        pending = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in applied)
        if not pending:
            log.info("schema up to date", applied=len(applied))
            return 0

        for path in pending:
            log.info("applying migration", filename=path.name)
            # One transaction per file: a failure leaves no partial schema.
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                )

        log.info("migrations complete", applied=len(pending))
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
