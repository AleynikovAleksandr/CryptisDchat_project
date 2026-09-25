"""Скользящее окно на отсортированном множестве Redis (ключ ratelimit:{uuid|ip}:{endpoint})."""
from __future__ import annotations

import time
import uuid

from redis.asyncio import Redis

from app.interfaces.rate_limit import IRateLimiter, RateLimitResult


class RedisRateLimiter(IRateLimiter):
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.time()
        rkey = f"ratelimit:{key}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(rkey, 0, now - window_seconds)
            pipe.zadd(rkey, {f"{now}:{uuid.uuid4().hex[:8]}": now})
            pipe.zcard(rkey)
            pipe.zrange(rkey, 0, 0, withscores=True)
            pipe.expire(rkey, window_seconds + 1)
            _, _, count, oldest, _ = await pipe.execute()
        if count > limit:
            retry = int(window_seconds - (now - oldest[0][1])) + 1 if oldest else window_seconds
            return RateLimitResult(allowed=False, remaining=0, retry_after=max(retry, 1))
        return RateLimitResult(allowed=True, remaining=limit - count, retry_after=0)


def parse_limit(spec: str) -> tuple[int, int]:
    """"120/60" → (120 запросов, 60 секунд)."""
    count, window = spec.split("/", 1)
    return int(count), int(window)
