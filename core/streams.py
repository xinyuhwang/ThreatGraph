"""Redis Streams task transport.

One stream per pipeline stage, each with a single consumer group.

Why not one shared stream with a ``type`` field routing messages to three
groups? Because consumer groups do not filter by content. *Every* consumer
group on a stream receives *every* message in that stream — groups isolate
progress, and consumers within a group split work. With a shared stream the
analyst group would receive every enrichment message and have to acknowledge
and discard it; anything it discarded without acknowledging would sit pending,
be reclaimed, retried, and eventually land in the dead-letter stream. Every
task would generate spurious DLQ entries.

Separate streams also let each stage carry its own reclaim threshold, which
matters because enrichment and AI analysis have very different latencies.
"""

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from core.logging import get_logger

log = get_logger(__name__)

STREAM_ENRICH = "tasks:enrich"
STREAM_ANALYZE = "tasks:analyze"
STREAM_CORRELATE = "tasks:correlate"
STREAM_DLQ = "tasks:dlq"

GROUP_ENRICH = "enrichment-cg"
GROUP_ANALYZE = "analyst-cg"
GROUP_CORRELATE = "correlation-cg"

# Stream -> consumer group. The DLQ has no group; it is read by a human.
STAGE_GROUPS: dict[str, str] = {
    STREAM_ENRICH: GROUP_ENRICH,
    STREAM_ANALYZE: GROUP_ANALYZE,
    STREAM_CORRELATE: GROUP_CORRELATE,
}

MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Task:
    """One unit of work. The investigation ID is the only payload it needs —
    everything else is read from PostgreSQL, so a task can never carry stale
    state."""

    message_id: str
    investigation_id: str
    stream: str
    #: Redis' delivery counter. 1 on first delivery, higher after a reclaim.
    attempt: int = 1


class TaskStream:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def ensure_groups(self) -> None:
        """Create each stream and its consumer group if they do not exist."""
        for stream, group in STAGE_GROUPS.items():
            try:
                await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
                log.info("consumer group created", stream=stream, group=group)
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

    async def publish(self, stream: str, investigation_id: str) -> str:
        message_id = await self.redis.xadd(stream, {"investigation_id": investigation_id})
        log.info("task published", stream=stream, message_id=message_id)
        return message_id

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 1,
        block_ms: int = 5_000,
    ) -> list[Task]:
        """Read undelivered messages. Returns an empty list on timeout."""
        response = await self.redis.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=count,
            block=block_ms,
        )
        if not response:
            return []

        tasks: list[Task] = []
        for _stream_name, messages in response:
            for message_id, fields in messages:
                investigation_id = fields.get("investigation_id")
                if not investigation_id:
                    # Malformed and unprocessable. Acknowledge so it does not
                    # sit pending forever, and record it for inspection.
                    log.error("task missing investigation_id", message_id=message_id)
                    await self.ack(stream, group, message_id)
                    continue
                tasks.append(
                    Task(message_id=message_id, investigation_id=investigation_id, stream=stream)
                )
        return tasks

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        await self.redis.xack(stream, group, message_id)

    async def send_to_dlq(self, task: Task, group: str, reason: str) -> None:
        """Copy to the dead-letter stream, then acknowledge the original.

        Acknowledging matters: an unacknowledged message stays pending and is
        reclaimed forever, so a task can otherwise be dead-lettered repeatedly.

        The group is passed in rather than looked up, so this works for any
        stream — including the isolated ones the reliability tests use, which
        must not collide with the live pipeline.
        """
        await self.redis.xadd(
            STREAM_DLQ,
            {
                "investigation_id": task.investigation_id,
                "origin_stream": task.stream,
                "origin_message_id": task.message_id,
                "attempts": str(task.attempt),
                "reason": reason,
            },
        )
        await self.ack(task.stream, group, task.message_id)
        log.error(
            "task dead-lettered",
            stream=task.stream,
            message_id=task.message_id,
            attempts=task.attempt,
            reason=reason,
        )

    async def depth(self, stream: str) -> int:
        return await self.redis.xlen(stream)

    async def pending(self, stream: str, group: str) -> int:
        summary: dict[str, Any] = await self.redis.xpending(stream, group)
        return summary.get("pending", 0) if summary else 0

    async def reclaim(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int,
        count: int = 10,
    ) -> list[Task]:
        """Take over messages a worker received but never acknowledged.

        A crash between delivery and acknowledgment leaves the message
        pending. ``XREADGROUP >`` only ever delivers new messages, so without
        this the task is never touched again and its investigation stalls
        permanently.

        ``XPENDING`` is used rather than ``XAUTOCLAIM`` because it reports the
        delivery count, which is what decides between another attempt and the
        dead-letter stream.
        """
        entries = await self.redis.xpending_range(
            stream, group, min="-", max="+", count=count, idle=min_idle_ms
        )
        if not entries:
            return []

        tasks: list[Task] = []
        for entry in entries:
            message_id = entry["message_id"]
            delivered = entry["times_delivered"]

            # XCLAIM with the same idle threshold is atomic: if another worker
            # claimed this message first, the idle timer reset and this call
            # returns nothing. Only one worker can win.
            claimed = await self.redis.xclaim(
                stream, group, consumer, min_idle_time=min_idle_ms, message_ids=[message_id]
            )
            if not claimed:
                continue

            _claimed_id, fields = claimed[0]
            if not fields:
                # The message body was deleted out from under the group.
                # Acknowledge so it stops being reclaimed forever.
                log.warning("reclaimed an empty message, discarding", message_id=message_id)
                await self.ack(stream, group, message_id)
                continue

            task = Task(
                message_id=message_id,
                investigation_id=fields["investigation_id"],
                stream=stream,
                attempt=delivered + 1,
            )

            log.warning(
                "reclaimed a stalled task",
                stream=stream,
                message_id=message_id,
                attempt=task.attempt,
                idle_ms=entry["time_since_delivered"],
            )
            # Returned even when over the attempt limit. The worker owns the
            # dead-letter decision because giving up on a task must also mark
            # the investigation failed — otherwise the sweeper sees it still
            # sitting in a non-terminal status and republishes it forever.
            tasks.append(task)

        return tasks


async def create_redis(url: str, *, socket_timeout: float = 5.0) -> Redis:
    """Create a Redis client.

    ``socket_timeout`` must exceed the longest blocking call made on this
    client. A worker calling ``XREADGROUP BLOCK 5000`` holds the socket open
    for five seconds with nothing to read; a shorter socket timeout treats
    that healthy idle wait as a connection failure. Callers that block should
    pass their block duration plus a margin — see :func:`workers.base.run_worker`.
    """
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_keepalive=True,
    )
