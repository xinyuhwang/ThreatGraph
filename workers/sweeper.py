"""Recovers investigations that stopped advancing with no task in flight.

The reclaim loop in :mod:`core.streams` handles messages a worker received and
never acknowledged. It cannot help with the opposite failure: a worker that
acknowledged its message and then died before publishing the next stage's
task. There is no pending message to reclaim, nothing in any stream, and no
worker will ever look at that investigation again.

Only the database knows. This process looks for investigations sitting in a
non-terminal status with no recent activity and republishes a task for the
stage they are stuck in. Re-running a stage is safe because every write in the
pipeline is idempotent, and the stage workers accept their own working status
as a valid entry point so a resumed task is not refused.

The staleness threshold must comfortably exceed the slowest stage, or the
sweeper will re-queue work that is simply still running.
"""

import asyncio
import signal

from core.config import settings
from core.db import Database
from core.lifecycle import Status
from core.logging import configure_logging, get_logger, investigation_context
from core.streams import (
    STREAM_ANALYZE,
    STREAM_CORRELATE,
    STREAM_ENRICH,
    TaskStream,
    create_redis,
)

log = get_logger(__name__)

#: Which stream restarts each stage. An investigation still in `pending` never
#: had its task published — or the publish failed after the row was written,
#: which the API deliberately does not roll back.
STAGE_RECOVERY: dict[Status, str] = {
    Status.PENDING: STREAM_ENRICH,
    Status.ENRICHING: STREAM_ENRICH,
    Status.ANALYZING: STREAM_ANALYZE,
    Status.CORRELATING: STREAM_CORRELATE,
}

STALE_QUERY = """
SELECT id, indicator, status, updated_at
  FROM investigations
 WHERE status NOT IN ('complete', 'failed', 'cancelled')
   AND updated_at < now() - ($1 || ' seconds')::interval
 ORDER BY updated_at
 LIMIT $2
"""


class Sweeper:
    def __init__(self, db: Database, tasks: TaskStream) -> None:
        self.db = db
        self.tasks = tasks
        self._running = True

    async def sweep_once(self) -> int:
        rows = await self.db.fetch(
            STALE_QUERY, str(settings.sweeper_stale_after_seconds), settings.sweeper_batch_size
        )
        if not rows:
            return 0

        recovered = 0
        for row in rows:
            investigation_id = str(row["id"])
            with investigation_context(investigation_id):
                status = Status(row["status"])
                stream = STAGE_RECOVERY.get(status)
                if stream is None:
                    log.error("no recovery stream for status", status=status.value)
                    continue

                # Touch updated_at so a slow stage is not swept repeatedly
                # while the republished task is being worked.
                await self.db.execute(
                    "UPDATE investigations SET updated_at = now() WHERE id = $1", row["id"]
                )
                await self.tasks.publish(stream, investigation_id)
                recovered += 1
                log.warning(
                    "republished a stalled investigation",
                    indicator=row["indicator"],
                    status=status.value,
                    stream=stream,
                    stalled_since=row["updated_at"],
                )

        return recovered

    async def run(self) -> None:
        log.info(
            "sweeper started",
            interval_seconds=settings.sweeper_interval_seconds,
            stale_after_seconds=settings.sweeper_stale_after_seconds,
        )
        while self._running:
            try:
                recovered = await self.sweep_once()
                if recovered:
                    log.warning("sweep recovered investigations", count=recovered)
            except Exception:
                log.exception("sweep failed")

            # Short sleeps so shutdown stays responsive.
            for _ in range(settings.sweeper_interval_seconds):
                if not self._running:
                    break
                await asyncio.sleep(1)

        log.info("sweeper stopped")

    def request_stop(self) -> None:
        self._running = False


async def main() -> None:
    configure_logging()

    db = Database(settings.database_url)
    await db.connect(min_size=1, max_size=3)
    redis = await create_redis(settings.redis_url)
    tasks = TaskStream(redis)
    await tasks.ensure_groups()

    sweeper = Sweeper(db, tasks)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, sweeper.request_stop)

    try:
        await sweeper.run()
    finally:
        await db.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
