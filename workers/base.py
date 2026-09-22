"""Shared worker loop.

Each stage subclasses :class:`Worker`, declares which status it advances
through and where it publishes next, and implements ``handle``. The loop
around that — cancellation checks, conditional transitions, ordering of publish
and acknowledge, failure classification — is identical for every stage and
lives here.
"""

import asyncio
import os
import signal
import socket
from abc import ABC, abstractmethod

from core.config import settings
from core.db import Database
from core.lifecycle import Status, advance, is_cancelled, mark_failed
from core.logging import configure_logging, get_logger, investigation_context
from core.streams import STAGE_GROUPS, Task, TaskStream, create_redis

log = get_logger(__name__)


class PermanentError(Exception):
    """The task can never succeed — an invalid indicator, a schema violation.

    Marks the investigation failed and acknowledges the message. Distinct from
    an ordinary exception, which leaves the message unacknowledged so it can be
    retried.
    """


class Worker(ABC):
    #: Stream this worker consumes from.
    stream: str
    #: Status the investigation must be in for this worker to act.
    expected_status: Status
    #: Status set while this worker is working.
    working_status: Status
    #: Where to publish on success. None for the last stage.
    next_stream: str | None = None
    #: Status set after ``handle`` succeeds. Only the last stage sets this.
    final_status: Status | None = None

    def __init__(self, db: Database, tasks: TaskStream) -> None:
        self.db = db
        self.tasks = tasks
        self.group = STAGE_GROUPS[self.stream]
        self.consumer = f"{socket.gethostname()}-{os.getpid()}"
        self._running = True

    @abstractmethod
    async def handle(self, investigation_id: str) -> None:
        """Do this stage's work. Raise to fail; raise PermanentError to fail
        without retrying."""

    async def run(self) -> None:
        log.info("worker started", stream=self.stream, consumer=self.consumer)
        while self._running:
            try:
                tasks = await self.tasks.consume(
                    self.stream,
                    self.group,
                    self.consumer,
                    block_ms=settings.worker_block_ms,
                )
            except Exception:
                log.exception("consume failed, backing off", stream=self.stream)
                await asyncio.sleep(1)
                continue

            for task in tasks:
                with investigation_context(task.investigation_id):
                    await self._process(task)

        log.info("worker stopped", stream=self.stream)

    async def _process(self, task: Task) -> None:
        investigation_id = task.investigation_id

        # Cancellation is checked at the stage boundary, before any work.
        if await is_cancelled(self.db, investigation_id):
            log.info("investigation cancelled, skipping stage", stream=self.stream)
            await self._ack(task)
            return

        # Conditional transition. A False return means another worker already
        # advanced this investigation — normal under at-least-once delivery,
        # so acknowledge and move on rather than retrying.
        if not await advance(self.db, investigation_id, self.expected_status, self.working_status):
            await self._ack(task)
            return

        try:
            await self.handle(investigation_id)
        except PermanentError as exc:
            await mark_failed(self.db, investigation_id, str(exc))
            await self._ack(task)
            return
        except Exception:
            # Leave the message unacknowledged. It stays pending and the
            # reclaim loop (Day 3) redelivers it to another worker.
            log.exception("stage failed, leaving task for retry", stream=self.stream)
            return

        if self.final_status is not None:
            await advance(self.db, investigation_id, self.working_status, self.final_status)

        # Publish before acknowledging, deliberately.
        #
        # Acknowledge-then-publish leaves no pending message and no next task
        # if the worker dies in between: the investigation is stranded
        # silently, forever. Publish-then-acknowledge instead redelivers this
        # task, which republishes the next one — a duplicate, which the
        # uniqueness constraints absorb. Duplicates are safe here; lost work
        # is not.
        if self.next_stream is not None:
            await self.tasks.publish(self.next_stream, investigation_id)

        await self._ack(task)

    async def _ack(self, task: Task) -> None:
        await self.tasks.ack(task.stream, self.group, task.message_id)

    def request_stop(self) -> None:
        self._running = False


async def run_worker(worker_class: type[Worker]) -> None:
    """Wire up dependencies, install signal handlers, and run until stopped."""
    configure_logging()

    db = Database(settings.database_url)
    await db.connect(min_size=1, max_size=5)

    # The socket must outlive the blocking read, or every idle XREADGROUP
    # looks like a dropped connection.
    redis = await create_redis(
        settings.redis_url,
        socket_timeout=settings.worker_block_ms / 1000 + 5,
    )
    tasks = TaskStream(redis)
    await tasks.ensure_groups()

    worker = worker_class(db, tasks)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_stop)

    try:
        await worker.run()
    finally:
        await db.close()
        await redis.aclose()
