"""Shared dependencies, resolved from application state."""

from fastapi import Request

from core.db import Database
from core.streams import TaskStream


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_tasks(request: Request) -> TaskStream:
    return request.app.state.tasks


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    Direct socket address. Behind a reverse proxy this would need to read a
    trusted X-Forwarded-For instead — trusting that header unconditionally
    would let any client forge its own identity.
    """
    return request.client.host if request.client else "unknown"
