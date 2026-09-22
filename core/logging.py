"""Structured JSON logging with trace IDs carried in context variables.

Two IDs, because they have different lifetimes. ``request_id`` exists for every
HTTP request, including ones that fail before an investigation is created.
``investigation_id`` is attached once there is an investigation, and is what ties
a single investigation's log lines together across the API and all three
workers.
"""

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_investigation_id: ContextVar[str | None] = ContextVar("investigation_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if request_id := _request_id.get():
            payload["request_id"] = request_id
        if investigation_id := _investigation_id.get():
            payload["investigation_id"] = investigation_id

        payload.update(getattr(record, "context", {}))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class BoundLogger:
    """Thin wrapper giving stdlib logging a keyword-argument interface.

    ``log.info("task consumed", stream="tasks:enrich")`` instead of building
    an ``extra`` dict at every call site.
    """

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(self, level: int, message: str, **context: Any) -> None:
        self._logger.log(level, message, extra={"context": context})

    def debug(self, message: str, **context: Any) -> None:
        self._log(logging.DEBUG, message, **context)

    def info(self, message: str, **context: Any) -> None:
        self._log(logging.INFO, message, **context)

    def warning(self, message: str, **context: Any) -> None:
        self._log(logging.WARNING, message, **context)

    def error(self, message: str, **context: Any) -> None:
        self._log(logging.ERROR, message, **context)

    def exception(self, message: str, **context: Any) -> None:
        self._logger.exception(message, extra={"context": context})


def get_logger(name: str) -> BoundLogger:
    return BoundLogger(name)


def configure_logging(level: str | None = None) -> None:
    from core.config import settings

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level or settings.log_level)

    # uvicorn installs its own handlers; route them through ours so every line
    # in the container is JSON.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True


def set_request_id(value: str | None) -> None:
    _request_id.set(value)


def get_request_id() -> str | None:
    return _request_id.get()


def set_investigation_id(value: str | None) -> None:
    _investigation_id.set(value)


@contextmanager
def investigation_context(investigation_id: str) -> Iterator[None]:
    """Attach an investigation ID to every log line emitted inside the block."""
    token = _investigation_id.set(investigation_id)
    try:
        yield
    finally:
        _investigation_id.reset(token)
