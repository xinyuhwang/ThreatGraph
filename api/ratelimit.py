"""Per-client submission rate limiting.

Kept in Redis rather than in process memory, because the API is designed to be
stateless and horizontally scalable — an in-memory counter would give each
replica its own independent limit.

A fixed window, not a sliding one. It permits a burst at a window boundary, but
the purpose here is keeping queue depth bounded rather than precise fairness,
and it costs one round trip.
"""

import time

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from api.deps import client_key
from core.config import settings


async def enforce_rate_limit(request: Request) -> None:
    redis: Redis = request.app.state.redis
    window = int(time.time() // 60)
    key = f"ratelimit:{client_key(request)}:{window}"

    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, 120)
        count, _ = await pipe.execute()

    if count > settings.rate_limit_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded: {settings.rate_limit_per_minute} submissions "
                "per minute. Retry shortly."
            ),
            headers={"Retry-After": "60"},
        )
