"""AI analyst worker: interprets collected evidence.

The reasoning layer. It collects nothing — it reads what the enrichment
pipeline recorded, and produces a conclusion whose citations are verified
against the database before they are stored.
"""

import asyncio

from analyst.agent import AnalysisFailed, analyse, load_evidence
from analyst.factory import build_client
from core.lifecycle import Status
from core.logging import get_logger
from core.streams import STREAM_ANALYZE, STREAM_CORRELATE
from workers.base import PermanentError, Worker, run_worker

log = get_logger(__name__)


class AnalystWorker(Worker):
    stream = STREAM_ANALYZE
    expected_status = Status.ENRICHING
    working_status = Status.ANALYZING
    next_stream = STREAM_CORRELATE

    async def handle(self, investigation_id: str) -> None:
        evidence = await load_evidence(self.db, investigation_id)
        primary = evidence["primary_entity"]

        log.info(
            "analysis started",
            observations=len(evidence["observations"]),
            enrichment_status=evidence["enrichment_status"],
        )

        client = build_client(
            indicator=evidence["indicator"],
            primary_entity_id=str(primary["id"]) if primary else None,
        )
        try:
            await analyse(self.db, investigation_id, client)
        except AnalysisFailed as exc:
            # Not retryable: re-running produces the same outcome and costs
            # another set of API calls. Record why and let the investigation
            # finish as failed, still queryable.
            raise PermanentError(str(exc)) from exc
        finally:
            await client.close()


if __name__ == "__main__":
    asyncio.run(run_worker(AnalystWorker))
