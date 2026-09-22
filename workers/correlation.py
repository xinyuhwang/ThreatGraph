"""Correlation worker: records how entities connect, then closes out.

Day 1 skeleton — consumes tasks and marks the investigation complete.
Relationship creation arrives on Day 2.

Note that this stage creates *relationships* only. Entities are upserted during
enrichment, because observations carry an entity foreign key and the AI analyst
needs entity IDs — both of which happen before correlation runs.
"""

import asyncio

from core.lifecycle import Status
from core.logging import get_logger
from core.streams import STREAM_CORRELATE
from workers.base import Worker, run_worker

log = get_logger(__name__)


class CorrelationWorker(Worker):
    stream = STREAM_CORRELATE
    expected_status = Status.ANALYZING
    working_status = Status.CORRELATING
    next_stream = None
    final_status = Status.COMPLETE

    async def handle(self, investigation_id: str) -> None:
        log.info("correlation stage reached")

        # Day 2:
        #   - read the observations and derive relationships between the
        #     entities enrichment discovered (domain resolves_to ip, ...)
        #   - assign confidence by deterministic rule, never by the AI: a DNS
        #     A record is 1.0; an inferred shared-hosting link scores lower
        #   - INSERT ... ON CONFLICT DO NOTHING on the uniqueness triple


if __name__ == "__main__":
    asyncio.run(run_worker(CorrelationWorker))
