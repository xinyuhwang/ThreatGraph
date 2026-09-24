"""Fixtures shared by every tier that needs live infrastructure."""

import pytest

from core.config import settings
from core.db import Database
from core.streams import create_redis


@pytest.fixture
async def db():
    database = Database(settings.database_url)
    await database.connect(min_size=1, max_size=3)
    yield database
    await database.close()


@pytest.fixture
async def redis():
    client = await create_redis(settings.redis_url, socket_timeout=10)
    yield client
    await client.aclose()
