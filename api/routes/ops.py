"""Operational endpoints."""

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import PlainTextResponse

from api.deps import get_db
from api.models import DependencyHealth
from core.db import Database
from core.metrics import render
from core.streams import STAGE_GROUPS

router = APIRouter(tags=["operations"])


@router.get("/health", response_model=DependencyHealth, summary="Liveness and dependency check")
async def health(
    request: Request,
    response: Response,
    db: Database = Depends(get_db),
) -> DependencyHealth:
    """Report per-dependency status.

    Returns 503 when a dependency is down, so the Compose healthcheck and any
    load balancer can act on it. The body still lists which dependency failed.
    """
    dependencies = {"postgres": "ok" if await db.ping() else "unavailable"}

    try:
        await request.app.state.redis.ping()
        dependencies["redis"] = "ok"
    except Exception:
        dependencies["redis"] = "unavailable"

    healthy = all(state == "ok" for state in dependencies.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return DependencyHealth(status="ok" if healthy else "degraded", dependencies=dependencies)


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="Prometheus metrics",
    responses={200: {"content": {"text/plain": {}}}},
)
async def metrics(request: Request, db: Database = Depends(get_db)) -> str:
    """Counts, queue depth, and stage latency in Prometheus exposition format.

    Latency is a histogram rather than a counter — an average would hide the
    slow tail, which is the reason to look.
    """
    return await render(db, request.app.state.redis, STAGE_GROUPS)
