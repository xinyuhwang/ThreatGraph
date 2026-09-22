"""Investigation status transitions.

Transitions are durable and conditional. Because Redis delivers at-least-once,
a worker will sometimes pick up a task for an investigation that has already
moved past its stage — so every transition is expressed as an UPDATE guarded by
the status the worker expects to find. Matching zero rows is the normal,
expected outcome in that case, not an error.
"""

from enum import StrEnum

from core.db import Database
from core.logging import get_logger

log = get_logger(__name__)


class Status(StrEnum):
    PENDING = "pending"
    ENRICHING = "enriching"
    ANALYZING = "analyzing"
    CORRELATING = "correlating"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL: frozenset[Status] = frozenset({Status.COMPLETE, Status.FAILED, Status.CANCELLED})

VALID_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.PENDING: frozenset({Status.ENRICHING, Status.FAILED, Status.CANCELLED}),
    Status.ENRICHING: frozenset({Status.ANALYZING, Status.FAILED, Status.CANCELLED}),
    Status.ANALYZING: frozenset({Status.CORRELATING, Status.FAILED, Status.CANCELLED}),
    Status.CORRELATING: frozenset({Status.COMPLETE, Status.FAILED, Status.CANCELLED}),
    Status.COMPLETE: frozenset(),
    Status.FAILED: frozenset(),
    Status.CANCELLED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when code asks for a transition the state machine does not allow.

    This is a programming error, distinct from losing a race with another
    worker — that returns False from advance() instead.
    """


def is_valid(source: Status, target: Status) -> bool:
    return target in VALID_TRANSITIONS[source]


async def get_status(db: Database, investigation_id: str) -> Status | None:
    value = await db.fetchval("SELECT status FROM investigations WHERE id = $1", investigation_id)
    return Status(value) if value else None


async def advance(
    db: Database,
    investigation_id: str,
    expected: Status,
    target: Status,
) -> bool:
    """Move an investigation forward, but only from ``expected``.

    Returns True if this call performed the transition, and False if the
    investigation was not in the expected state — because another worker
    already advanced it, or because it was cancelled. A False return means the
    caller should acknowledge its message and stop, not retry.
    """
    if not is_valid(expected, target):
        raise InvalidTransition(f"{expected} -> {target} is not a permitted transition")

    updated = await db.fetchval(
        """
        UPDATE investigations
           SET status = $3, updated_at = now()
         WHERE id = $1 AND status = $2
        RETURNING id
        """,
        investigation_id,
        expected.value,
        target.value,
    )

    if updated is None:
        actual = await get_status(db, investigation_id)
        log.info(
            "transition skipped, investigation already advanced",
            expected=expected.value,
            target=target.value,
            actual=actual.value if actual else None,
        )
        return False

    log.info("status advanced", **{"from": expected.value, "to": target.value})
    return True


async def mark_failed(db: Database, investigation_id: str, error: str) -> None:
    """Terminal failure from any non-terminal state. The investigation stays
    queryable so a client polling it sees why it stopped."""
    await db.execute(
        """
        UPDATE investigations
           SET status = 'failed', error = $2, updated_at = now()
         WHERE id = $1
           AND status NOT IN ('complete', 'failed', 'cancelled')
        """,
        investigation_id,
        error[:1000],
    )
    log.error("investigation failed", error=error)


async def is_cancelled(db: Database, investigation_id: str) -> bool:
    """Checked by each worker at the start of its stage. Cancellation takes
    effect at the next stage boundary rather than interrupting work in flight."""
    return await get_status(db, investigation_id) == Status.CANCELLED
