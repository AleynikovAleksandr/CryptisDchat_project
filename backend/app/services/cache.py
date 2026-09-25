"""Кэш горячих данных в Redis (Gunicorn_Celery.md 3.3).

Все значения производные: при потере пересчитываются из БД.
"""
from __future__ import annotations

from redis.asyncio import Redis

PRESENCE_TTL = 60


def _unread_key(user_id: str, thread_id: str) -> str:
    return f"unread:{user_id}:{thread_id}"


async def touch_presence(redis: Redis, user_id: str) -> None:
    await redis.set(f"presence:{user_id}", "1", ex=PRESENCE_TTL)


async def clear_presence(redis: Redis, user_id: str) -> None:
    await redis.delete(f"presence:{user_id}")


async def online_users(redis: Redis, user_ids: list[str]) -> set[str]:
    if not user_ids:
        return set()
    values = await redis.mget([f"presence:{u}" for u in user_ids])
    return {u for u, v in zip(user_ids, values) if v}


async def incr_unread(redis: Redis, user_id: str, thread_id: str) -> None:
    await redis.incr(_unread_key(user_id, thread_id))


async def reset_unread(redis: Redis, user_id: str, thread_id: str) -> None:
    await redis.set(_unread_key(user_id, thread_id), 0)


async def set_unread(redis: Redis, user_id: str, thread_id: str, value: int) -> None:
    await redis.set(_unread_key(user_id, thread_id), value)


async def get_unread(redis: Redis, user_id: str, thread_ids: list[str]) -> dict[str, int | None]:
    if not thread_ids:
        return {}
    values = await redis.mget([_unread_key(user_id, t) for t in thread_ids])
    return {t: (int(v) if v is not None else None) for t, v in zip(thread_ids, values)}


async def invalidate_threads(redis: Redis, *user_ids: str) -> None:
    if user_ids:
        await redis.delete(*[f"threads:{u}" for u in user_ids])


CHAIN_PENDING_KEY = "chain:pending"


async def push_chain_event(redis: Redis, event_id: str) -> int:
    """Очередь ожидания хэшей перед батчингом (3.3); возвращает длину очереди."""
    return await redis.rpush(CHAIN_PENDING_KEY, event_id)
