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
        if pending:
            for path in pending:
                log.info("applying migration", filename=path.name)
                # One transaction per file: a failure leaves no partial schema.
                async with conn.transaction():
                    await conn.execute(path.read_text())
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
                    )
            log.info("migrations complete", applied=len(pending))
        else:
            log.info("schema up to date", applied=len(applied))

        await load_seed(conn)
        return 0
    finally:
        await conn.close()


async def load_seed(conn: asyncpg.Connection) -> None:
    """Load demo data, if enabled and not already present.

    Two of the analyst's five tools query history, so a database with none
    exercises the pipeline without ever showing the capability the graph
    exists for. Every statement in the seed is an upsert, so running it
    repeatedly is harmless.
    """
    if not settings.seed_demo_data:
        return

    seed_path = pathlib.Path(__file__).parent.parent / "seeds" / "demo.sql"
    if not seed_path.exists():
        log.warning("seed enabled but seeds/demo.sql is missing")
        return

    async with conn.transaction():
        await conn.execute(seed_path.read_text())

    count = await conn.fetchval(
        "SELECT count(*) FROM investigations WHERE indicator LIKE '%.example'"
    )
    log.info("demo data loaded", seeded_investigations=count)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
