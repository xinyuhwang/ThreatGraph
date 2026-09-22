"""Correlation worker: records how entities connect, then closes out.

This stage creates *relationships* only. Entities are upserted during
enrichment, because observations carry an entity foreign key and the AI
analyst needs entity IDs — both of which happen before correlation runs.

Confidence is assigned here by rule, never by the AI. The platform's core
principle is that the model interprets evidence rather than producing it, and
a confidence score attached to a stored relationship is data, not
interpretation.
"""

import asyncio
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from core.indicators import hostname_of
from core.lifecycle import Status
from core.logging import get_logger
from core.store import create_relationship, get_observation_map, upsert_entity
from core.streams import STREAM_CORRELATE
from workers.base import Worker, run_worker

log = get_logger(__name__)

# Confidence by rule. Everything below is a direct observation, so all score
# 1.0 — the DNS resolver or the HTTP server told us this outright.
#
# The column exists for inferred edges, which arrive with the enrichment
# sources that support them: two domains sharing a TLS certificate, or a
# registrar and registration date that match across a cluster, are real signals
# but weaker than a resolved A record, and should be recorded as such.
CONFIDENCE: dict[str, float] = {
    "resolves_to": 1.0,  # DNS A/AAAA record
    "redirects_to": 1.0,  # observed hop in an HTTP redirect chain
    "hosted_on": 1.0,  # a URL is served by its own hostname
}


class CorrelationWorker(Worker):
    stream = STREAM_CORRELATE
    expected_status = Status.ANALYZING
    working_status = Status.CORRELATING
    next_stream = None
    final_status = Status.COMPLETE

    async def handle(self, investigation_id: str) -> None:
        row = await self.db.fetchrow(
            "SELECT indicator, indicator_type FROM investigations WHERE id = $1",
            investigation_id,
        )
        indicator: str = row["indicator"]
        indicator_type: str = row["indicator_type"]
        host = hostname_of(indicator, indicator_type)

        observations = await get_observation_map(self.db, investigation_id)
        domain_id = await upsert_entity(self.db, "domain", host)

        created = 0
        created += await self._link_resolved_addresses(domain_id, observations.get("dns"))
        created += await self._link_redirect_chain(observations.get("http"))
        created += await self._link_url_to_host(indicator, indicator_type, domain_id)

        log.info("correlation complete", relationships_created=created)

    async def _link_resolved_addresses(self, domain_id: UUID, observation: Any) -> int:
        """domain --resolves_to--> ip, one edge per A/AAAA record."""
        if observation is None or observation["status"] != "ok":
            return 0

        created = 0
        for address in observation["data"].get("resolved_ips", []):
            ip_id = await upsert_entity(self.db, "ip", address)
            if await create_relationship(
                self.db, domain_id, ip_id, "resolves_to", CONFIDENCE["resolves_to"]
            ):
                created += 1
        return created

    async def _link_redirect_chain(self, observation: Any) -> int:
        """domain --redirects_to--> domain, for each hop that crosses hosts.

        Same-host redirects are path changes, not infrastructure links, so
        they are skipped — recording them would fill the graph with self-edges
        that say nothing.
        """
        if observation is None or observation["status"] != "ok":
            return 0

        chain: list[str] = observation["data"].get("redirect_chain", [])
        hosts = [urlsplit(url).hostname for url in chain]

        created = 0
        for source_host, target_host in zip(hosts, hosts[1:], strict=False):
            if not source_host or not target_host or source_host == target_host:
                continue
            source_id = await upsert_entity(self.db, "domain", source_host)
            target_id = await upsert_entity(self.db, "domain", target_host)
            if await create_relationship(
                self.db, source_id, target_id, "redirects_to", CONFIDENCE["redirects_to"]
            ):
                created += 1
        return created

    async def _link_url_to_host(
        self, indicator: str, indicator_type: str, domain_id: UUID
    ) -> int:
        """url --hosted_on--> domain, so a URL investigation connects to the
        domain graph rather than sitting isolated."""
        if indicator_type != "url":
            return 0

        url_id = await upsert_entity(self.db, "url", indicator)
        created = await create_relationship(
            self.db, url_id, domain_id, "hosted_on", CONFIDENCE["hosted_on"]
        )
        return int(created)


if __name__ == "__main__":
    asyncio.run(run_worker(CorrelationWorker))
