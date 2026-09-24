"""Enrichment worker: collects evidence about the indicator.

DNS and HTTP run concurrently. If one fails the other still contributes, and
the failure itself is written as an observation — the analyst needs to know a
source was attempted and produced nothing, which is different from a source
that was never tried.
"""

import asyncio
from typing import Any
from uuid import UUID

from core.config import settings
from core.indicators import hostname_of
from core.lifecycle import Status
from core.logging import get_logger
from core.store import save_observation, set_enrichment_status, upsert_entity
from core.streams import STREAM_ANALYZE, STREAM_ENRICH
from enrichment import dns as dns_enrichment
from enrichment import http as http_enrichment
from workers.base import Worker, run_worker

log = get_logger(__name__)


def describe_failure(exc: BaseException) -> dict[str, Any]:
    """Shape a failed source into evidence the analyst can read."""
    return {"error": type(exc).__name__, "detail": str(exc)[:500]}


class EnrichmentWorker(Worker):
    stream = STREAM_ENRICH
    expected_status = Status.PENDING
    idle_reclaim_ms = settings.reclaim_idle_enrich_ms
    working_status = Status.ENRICHING
    next_stream = STREAM_ANALYZE

    async def handle(self, investigation_id: str) -> None:
        row = await self.db.fetchrow(
            "SELECT indicator, indicator_type FROM investigations WHERE id = $1",
            investigation_id,
        )
        indicator: str = row["indicator"]
        indicator_type: str = row["indicator_type"]
        host = hostname_of(indicator, indicator_type)

        log.info("enrichment started", indicator=indicator, host=host)

        # return_exceptions keeps one source's failure from cancelling the other.
        dns_result, http_result = await asyncio.gather(
            dns_enrichment.collect(host),
            http_enrichment.collect(indicator, indicator_type),
            return_exceptions=True,
        )

        entity_ids = await self._upsert_primary_entities(indicator, indicator_type, host)
        statuses: dict[str, str] = {}

        statuses["dns"] = await self._record(
            investigation_id,
            source="dns",
            result=dns_result,
            entity_id=entity_ids["domain"],
        )
        statuses["http"] = await self._record(
            investigation_id,
            source="http",
            result=http_result,
            entity_id=entity_ids["primary"],
        )

        # Every address either source observed becomes an entity, so later
        # investigations that land on the same address can be connected to
        # this one.
        await self._upsert_observed_ips(dns_result, http_result)

        await set_enrichment_status(self.db, investigation_id, statuses)
        log.info("enrichment complete", **statuses)

    async def _upsert_primary_entities(
        self, indicator: str, indicator_type: str, host: str
    ) -> dict[str, UUID]:
        """Create the entities this investigation is about.

        Entities are created here, during enrichment, rather than during
        correlation: observations carry an entity foreign key and the AI
        analyst needs entity IDs, and both happen before correlation runs.
        """
        domain_id = await upsert_entity(self.db, "domain", host)

        if indicator_type == "url":
            url_id = await upsert_entity(self.db, "url", indicator)
            return {"primary": url_id, "domain": domain_id}
        return {"primary": domain_id, "domain": domain_id}

    async def _record(
        self,
        investigation_id: str,
        *,
        source: str,
        result: Any,
        entity_id: UUID,
    ) -> str:
        """Persist one source's outcome and return 'ok' or 'failed'."""
        if isinstance(result, BaseException):
            log.warning(
                "enrichment source failed",
                source=source,
                error=type(result).__name__,
                detail=str(result)[:200],
            )
            await save_observation(
                self.db,
                investigation_id,
                source,
                describe_failure(result),
                entity_id=entity_id,
                status="failed",
            )
            return "failed"

        await save_observation(
            self.db, investigation_id, source, result, entity_id=entity_id, status="ok"
        )
        return "ok"

    async def _upsert_observed_ips(self, dns_result: Any, http_result: Any) -> None:
        addresses: set[str] = set()
        for result in (dns_result, http_result):
            if not isinstance(result, BaseException):
                addresses.update(result.get("resolved_ips", []))

        for address in sorted(addresses):
            await upsert_entity(self.db, "ip", address)

        if addresses:
            log.info("addresses recorded", count=len(addresses))


if __name__ == "__main__":
    asyncio.run(run_worker(EnrichmentWorker))
