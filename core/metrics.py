"""Metrics collection and Prometheus rendering.

Counts and queue depths are read live from PostgreSQL and Redis at scrape
time, so they need no bookkeeping. Stage duration is different: latency has a
distribution, and a distribution cannot be recovered after the fact from
current state. Workers therefore record each completed stage into Redis
counters shaped as a Prometheus histogram.

A counter would not do here. "Average stage duration" hides exactly what you
scrape metrics to find — the slow tail.
"""

from redis.asyncio import Redis

from core.db import Database
from core.logging import get_logger

log = get_logger(__name__)

HISTOGRAM_KEY = "metrics:stage_duration"

#: Upper bounds in seconds. Chosen to straddle the two very different stage
#: profiles: DNS and HTTP finish in well under a second, while an agentic
#: analyst loop runs for tens of seconds.
BUCKETS: tuple[float, ...] = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


async def observe_stage_duration(redis: Redis, stage: str, seconds: float) -> None:
    """Record one completed stage.

    Cumulative buckets, as Prometheus expects: an observation increments every
    bucket whose upper bound it falls within.
    """
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for bound in BUCKETS:
                if seconds <= bound:
                    pipe.hincrby(HISTOGRAM_KEY, f"{stage}:le:{bound}", 1)
            pipe.hincrby(HISTOGRAM_KEY, f"{stage}:count", 1)
            pipe.hincrbyfloat(HISTOGRAM_KEY, f"{stage}:sum", seconds)
            await pipe.execute()
    except Exception:
        # Metrics must never take down the pipeline they measure.
        log.warning("failed to record stage duration", stage=stage)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


async def render(db: Database, redis: Redis, streams: dict[str, str]) -> str:
    """Build the Prometheus exposition text."""
    lines: list[str] = []

    # -- investigations by status ------------------------------------------
    lines += [
        "# HELP threatgraph_investigations Investigations by lifecycle status.",
        "# TYPE threatgraph_investigations gauge",
    ]
    rows = await db.fetch("SELECT status, count(*) AS n FROM investigations GROUP BY status")
    counts = {row["status"]: row["n"] for row in rows}
    for status in (
        "pending",
        "enriching",
        "analyzing",
        "correlating",
        "complete",
        "failed",
        "cancelled",
    ):
        lines.append(f'threatgraph_investigations{{status="{status}"}} {counts.get(status, 0)}')

    # -- results by classification -----------------------------------------
    lines += [
        "# HELP threatgraph_results Analyst results by classification.",
        "# TYPE threatgraph_results gauge",
    ]
    rows = await db.fetch(
        "SELECT classification, count(*) AS n FROM investigation_results GROUP BY classification"
    )
    by_class = {row["classification"]: row["n"] for row in rows}
    for classification in ("benign", "suspicious", "malicious", "unknown"):
        lines.append(
            f'threatgraph_results{{classification="{classification}"}} '
            f"{by_class.get(classification, 0)}"
        )

    # -- queue depth and unacknowledged work -------------------------------
    lines += [
        "# HELP threatgraph_stream_length Total messages in each task stream.",
        "# TYPE threatgraph_stream_length gauge",
    ]
    depths: dict[str, int] = {}
    pendings: dict[str, int] = {}
    for stream, group in streams.items():
        depths[stream] = await redis.xlen(stream)
        summary = await redis.xpending(stream, group)
        pendings[stream] = (summary or {}).get("pending", 0)
        lines.append(f'threatgraph_stream_length{{stream="{_escape(stream)}"}} {depths[stream]}')

    lines += [
        "# HELP threatgraph_stream_pending Delivered but unacknowledged messages.",
        "# TYPE threatgraph_stream_pending gauge",
    ]
    for stream, pending in pendings.items():
        lines.append(f'threatgraph_stream_pending{{stream="{_escape(stream)}"}} {pending}')

    lines += [
        "# HELP threatgraph_dlq_length Tasks that exhausted their retries.",
        "# TYPE threatgraph_dlq_length gauge",
        f"threatgraph_dlq_length {await redis.xlen('tasks:dlq')}",
    ]

    # -- stage duration histogram ------------------------------------------
    raw = await redis.hgetall(HISTOGRAM_KEY)
    stages = sorted({key.split(":", 1)[0] for key in raw})
    if stages:
        lines += [
            "# HELP threatgraph_stage_duration_seconds Time spent in each pipeline stage.",
            "# TYPE threatgraph_stage_duration_seconds histogram",
        ]
        for stage in stages:
            label = _escape(stage)
            cumulative = 0
            for bound in BUCKETS:
                cumulative = int(raw.get(f"{stage}:le:{bound}", cumulative))
                lines.append(
                    f'threatgraph_stage_duration_seconds_bucket{{stage="{label}",'
                    f'le="{bound}"}} {cumulative}'
                )
            total = int(raw.get(f"{stage}:count", 0))
            lines.append(
                f'threatgraph_stage_duration_seconds_bucket{{stage="{label}",le="+Inf"}} {total}'
            )
            lines.append(
                f'threatgraph_stage_duration_seconds_sum{{stage="{label}"}} '
                f"{float(raw.get(f'{stage}:sum', 0.0))}"
            )
            lines.append(f'threatgraph_stage_duration_seconds_count{{stage="{label}"}} {total}')

    return "\n".join(lines) + "\n"
