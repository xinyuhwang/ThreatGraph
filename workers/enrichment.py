"""Enrichment worker: collects evidence about the indicator.

Day 1 skeleton — consumes tasks, moves the investigation through its stage, and
chains to analysis. DNS and HTTP collection arrive on Day 2.
"""

import asyncio

from core.lifecycle import Status
from core.logging import get_logger
from core.streams import STREAM_ANALYZE, STREAM_ENRICH
from workers.base import Worker, run_worker

log = get_logger(__name__)


class EnrichmentWorker(Worker):
    stream = STREAM_ENRICH
    expected_status = Status.PENDING
    working_status = Status.ENRICHING
    next_stream = STREAM_ANALYZE

    async def handle(self, investigation_id: str) -> None:
        indicator = await self.db.fetchval(
            "SELECT indicator FROM investigations WHERE id = $1", investigation_id
        )
        log.info("enrichment stage reached", indicator=indicator)

        # Day 2:
        #   - asyncio.gather DNS and HTTP with return_exceptions=True
        #   - upsert entities (the indicator, plus every resolved IP) so
        #     observations and the AI analyst have something to reference
        #   - write an observation per source, including failures, so the
        #     analyst can tell "returned nothing" from "never attempted"
        #   - record per-source outcome in investigations.enrichment_status


if __name__ == "__main__":
    asyncio.run(run_worker(EnrichmentWorker))
