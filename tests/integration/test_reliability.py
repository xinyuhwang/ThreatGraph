"""Recovery from worker failure.

"Idempotent workers, safe to restart" is one of this project's headline
claims. These tests are what make it a demonstrated property rather than an
assertion — they reproduce the two distinct ways work gets abandoned and show
that each one is recovered.
"""

import asyncio

import pytest

from core.lifecycle import Status, advance_or_resume, get_status
from core.streams import MAX_ATTEMPTS, Task
from workers.sweeper import Sweeper

pytestmark = pytest.mark.integration


async def deliver_without_ack(tasks, stream, group, investigation_id):
    """Simulate a worker that received a task and then died."""
    await tasks.publish(stream, investigation_id)
    delivered = await tasks.consume(stream, group, "doomed-worker", block_ms=100)
    assert delivered, "setup failed: message was not delivered"
    return delivered[0]


class TestReclaim:
    """Failure one: the worker died holding the message."""

    async def test_stalled_task_is_reclaimed(self, tasks, isolated_stream):
        stream, group = isolated_stream
        original = await deliver_without_ack(tasks, stream, group, "abc-123")

        # Any idle time qualifies, standing in for the threshold elapsing.
        await asyncio.sleep(0.05)
        reclaimed = await tasks.reclaim(stream, group, "healthy-worker", min_idle_ms=10)

        assert [t.message_id for t in reclaimed] == [original.message_id]
        assert reclaimed[0].investigation_id == "abc-123"
        assert reclaimed[0].attempt == 2, "delivery count should advance on reclaim"

    async def test_fresh_task_is_left_alone(self, tasks, isolated_stream):
        """A worker legitimately mid-stage must not have its task stolen."""
        stream, group = isolated_stream
        await deliver_without_ack(tasks, stream, group, "abc-123")

        reclaimed = await tasks.reclaim(stream, group, "impatient-worker", min_idle_ms=60_000)

        assert reclaimed == []

    async def test_acknowledged_task_is_not_reclaimed(self, tasks, isolated_stream):
        stream, group = isolated_stream
        task = await deliver_without_ack(tasks, stream, group, "abc-123")
        await tasks.ack(stream, group, task.message_id)

        await asyncio.sleep(0.05)
        assert await tasks.reclaim(stream, group, "worker", min_idle_ms=10) == []

    async def test_only_one_worker_wins_a_contested_task(self, tasks, isolated_stream):
        """XCLAIM is atomic, so two workers reclaiming at once cannot both
        get the same message and process it twice."""
        stream, group = isolated_stream
        await deliver_without_ack(tasks, stream, group, "abc-123")
        await asyncio.sleep(0.05)

        first, second = await asyncio.gather(
            tasks.reclaim(stream, group, "worker-a", min_idle_ms=10),
            tasks.reclaim(stream, group, "worker-b", min_idle_ms=10),
        )

        assert len(first) + len(second) == 1

    async def test_attempts_accumulate_across_reclaims(self, tasks, isolated_stream):
        stream, group = isolated_stream
        await deliver_without_ack(tasks, stream, group, "abc-123")

        attempts = []
        for index in range(3):
            await asyncio.sleep(0.05)
            reclaimed = await tasks.reclaim(stream, group, f"worker-{index}", min_idle_ms=10)
            attempts.append(reclaimed[0].attempt)

        assert attempts == [2, 3, 4]
        assert attempts[-1] > MAX_ATTEMPTS, "must eventually exceed the retry budget"


class TestDeadLetter:
    async def test_exhausted_task_is_dead_lettered_and_acknowledged(
        self, tasks, redis, isolated_stream
    ):
        """A dead-lettered message must stop being pending, or it is reclaimed
        and dead-lettered again forever."""
        stream, group = isolated_stream
        original = await deliver_without_ack(tasks, stream, group, "abc-123")
        task = Task(
            message_id=original.message_id,
            investigation_id="abc-123",
            stream=stream,
            attempt=MAX_ATTEMPTS + 1,
        )

        await tasks.send_to_dlq(task, group, "out of attempts")

        assert await tasks.pending(stream, group) == 0
        entries = await redis.xrange("tasks:dlq", "-", "+")
        assert any(
            fields.get("origin_message_id") == original.message_id for _id, fields in entries
        )


class TestResume:
    """Failure two: the message was acknowledged but the next stage never ran.

    Redis has nothing pending to reclaim here. Only the database knows, which
    is what the sweeper is for — and a swept task re-enters a stage the
    investigation is already sitting in.
    """

    async def test_a_stage_can_be_re_entered(self, db, investigation):
        """The investigation is already 'analyzing'; a swept task must be
        accepted rather than refused as an invalid transition."""
        assert await get_status(db, investigation["id"]) == Status.ANALYZING

        resumed = await advance_or_resume(
            db, investigation["id"], Status.ENRICHING, Status.ANALYZING
        )

        assert resumed is True

    async def test_a_completed_stage_is_not_re_entered(self, db, investigation):
        await db.execute(
            "UPDATE investigations SET status = 'complete' WHERE id = $1",
            investigation["id"],
        )

        resumed = await advance_or_resume(
            db, investigation["id"], Status.ENRICHING, Status.ANALYZING
        )

        assert resumed is False

    async def test_a_cancelled_investigation_is_not_resumed(self, db, investigation):
        await db.execute(
            "UPDATE investigations SET status = 'cancelled' WHERE id = $1",
            investigation["id"],
        )

        assert not await advance_or_resume(
            db, investigation["id"], Status.ENRICHING, Status.ANALYZING
        )


class TestSweeper:
    async def test_stale_investigation_is_republished(self, db, tasks, investigation):
        await db.execute(
            "UPDATE investigations SET updated_at = now() - interval '1 year' WHERE id = $1",
            investigation["id"],
        )

        recovered = await Sweeper(db, tasks).sweep_once()

        assert recovered >= 1

    async def test_recently_active_investigation_is_left_alone(self, db, tasks, investigation):
        """A stage that is simply slow must not be re-queued underneath itself."""
        await db.execute(
            "UPDATE investigations SET updated_at = now() WHERE id = $1",
            investigation["id"],
        )

        sweeper = Sweeper(db, tasks)
        swept_ids = []

        async def record(stream, investigation_id):
            swept_ids.append(investigation_id)

        sweeper.tasks = type("Stub", (), {"publish": staticmethod(record)})()
        await sweeper.sweep_once()

        assert investigation["id"] not in swept_ids

    async def test_sweeping_updates_the_timestamp(self, db, tasks, investigation):
        """Otherwise the same investigation is swept on every pass while the
        republished task is still being worked."""
        await db.execute(
            "UPDATE investigations SET updated_at = now() - interval '1 year' WHERE id = $1",
            investigation["id"],
        )

        await Sweeper(db, tasks).sweep_once()

        age = await db.fetchval(
            "SELECT extract(epoch from now() - updated_at) FROM investigations WHERE id = $1",
            investigation["id"],
        )
        assert age < 60
