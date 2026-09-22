"""PostgreSQL access: a connection pool and thin query helpers.

Raw SQL rather than an ORM. The interesting parts of this schema are the
uniqueness constraints and the ON CONFLICT clauses that rely on them, and those
read more clearly written out than expressed through a mapper.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from core.logging import get_logger

log = get_logger(__name__)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Hand jsonb back as dicts and lists instead of strings."""
    for type_name in ("json", "jsonb"):
        await conn.set_type_codec(
            type_name,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._pool

    async def connect(self, *, min_size: int = 2, max_size: int = 10) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=min_size,
            max_size=max_size,
            init=_init_connection,
        )
        log.info("database pool ready", min_size=min_size, max_size=max_size)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def execute(self, query: str, *args: Any) -> str:
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as conn, conn.transaction():
            yield conn

    async def ping(self) -> bool:
        try:
            return await self.fetchval("SELECT 1") == 1
        except Exception:
            log.exception("database ping failed")
            return False
