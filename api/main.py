"""FastAPI application: lifespan, trace-ID middleware, and error formatting."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.routes import investigations, ops
from core.config import settings
from core.db import Database
from core.logging import (
    configure_logging,
    get_logger,
    get_request_id,
    set_investigation_id,
    set_request_id,
)
from core.streams import TaskStream, create_redis

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()

    app.state.db = Database(settings.database_url)
    await app.state.db.connect()

    app.state.redis = await create_redis(settings.redis_url)
    app.state.tasks = TaskStream(app.state.redis)
    await app.state.tasks.ensure_groups()

    log.info("api ready")
    try:
        yield
    finally:
        await app.state.db.close()
        await app.state.redis.aclose()
        log.info("api stopped")


app = FastAPI(
    title="ThreatGraph",
    description=(
        "AI-powered threat intelligence and investigation platform.\n\n"
        "Submit a domain or URL; receive a classification in which every claim "
        "is traceable to a specific enrichment observation."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(investigations.router)
app.include_router(ops.router)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    """Give every request an ID, before anything can fail.

    This is why there are two trace IDs rather than one: an investigation ID
    cannot cover a validation error thrown before the investigation exists.
    """
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
    set_request_id(request_id)
    set_investigation_id(None)

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def _error(status_code: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "code": code, "request_id": get_request_id()},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    codes = {
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }
    response = _error(exc.status_code, str(exc.detail), codes.get(exc.status_code, "ERROR"))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    fields = "; ".join(
        f"{'.'.join(str(p) for p in err['loc'][1:])}: {err['msg']}" for err in exc.errors()
    )
    return _error(422, fields or "Invalid request", "VALIDATION_ERROR")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error", path=request.url.path)
    # The trace ID goes to the client; the detail stays in the logs.
    return _error(500, "Internal server error", "INTERNAL_ERROR")
