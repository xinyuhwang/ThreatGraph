"""AI analyst worker: interprets collected evidence.

Day 1 skeleton — consumes tasks, moves the investigation through its stage, and
chains to correlation. The Claude agent loop arrives on Day 2.
"""

import asyncio

from core.lifecycle import Status
from core.logging import get_logger
from core.streams import STREAM_ANALYZE, STREAM_CORRELATE
from workers.base import Worker, run_worker

log = get_logger(__name__)


class AnalystWorker(Worker):
    stream = STREAM_ANALYZE
    expected_status = Status.ENRICHING
    working_status = Status.ANALYZING
    next_stream = STREAM_CORRELATE

    async def handle(self, investigation_id: str) -> None:
        observations = await self.db.fetchval(
            "SELECT count(*) FROM observations WHERE investigation_id = $1", investigation_id
        )
        log.info("analysis stage reached", observation_count=observations)

        # Day 2:
        #   - load observations and enrichment_status as the evidence packet
        #   - call Claude with the five tools, prompt caching on the stable
        #     prefix, a hard iteration cap, and refusal handling
        #   - verify every returned evidence_ref exists AND belongs to this
        #     investigation; reject back to the model if not
        #   - persist the result and its FK-backed evidence links


if __name__ == "__main__":
    asyncio.run(run_worker(AnalystWorker))
