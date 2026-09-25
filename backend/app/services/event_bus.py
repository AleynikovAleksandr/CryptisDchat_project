"""Redis Pub/Sub-шина событий (Gunicorn_Celery.md 2.1, 3.1–3.3).

Воркеров Gunicorn несколько, и WebSocket пользователя A на воркере №1 не виден
воркеру №3. Любой процесс (воркер API или Celery) публикует событие в канал
`events:{user_uuid}`, а воркер, держащий сокет этого пользователя, подписан на канал.

Чтобы не открывать отдельное Redis-соединение на каждый из тысяч сокетов, в каждом
процессе работает один `PubSubHub`: одно соединение, подписка на канал при первом
локальном слушателе и отписка при последнем.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from redis.asyncio import Redis

from app.interfaces.events import IEventBus

log = logging.getLogger(__name__)

CHANNEL_PREFIX = "events:"


def channel(user_id: str) -> str:
    return f"{CHANNEL_PREFIX}{user_id}"


def encode_event(event: str, data: dict[str, Any]) -> str:
    return json.dumps({"event": event, "data": data}, separators=(",", ":"), default=str)


class RedisEventBus(IEventBus):
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self._listeners: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._pubsub = None
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def publish(self, user_id: str, event: str, data: dict[str, Any]) -> None:
        await self.redis.publish(channel(user_id), encode_event(event, data))

    async def _ensure_reader(self) -> None:
        if self._pubsub is None:
            self._pubsub = self.redis.pubsub(ignore_subscribe_messages=True)
        if self._reader is None or self._reader.done():
            self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._pubsub is not None
        while True:
            try:
                if not self._pubsub.subscribed:
                    await asyncio.sleep(0.05)
                    continue
                msg = await self._pubsub.get_message(timeout=1.0)
                if msg is None or msg.get("type") != "message":
                    continue
                name = msg["channel"]
                name = name.decode() if isinstance(name, bytes) else name
                payload = msg["data"]
                payload = payload.decode() if isinstance(payload, bytes) else payload
                decoded = json.loads(payload)
                user_id = name[len(CHANNEL_PREFIX):]
                for q in list(self._listeners.get(user_id, ())):
                    if q.full():
                        continue  # медленный клиент: событие пропускаем, истина в БД (3.2)
                    q.put_nowait((decoded["event"], decoded.get("data", {})))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — шина не должна падать от одного битого события
                log.exception("pubsub read loop error")
                await asyncio.sleep(0.5)

    async def subscribe(self, user_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        async with self._lock:
            await self._ensure_reader()
            first = not self._listeners[user_id]
            self._listeners[user_id].add(queue)
            if first:
                await self._pubsub.subscribe(channel(user_id))  # type: ignore[union-attr]
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._listeners[user_id].discard(queue)
                if not self._listeners[user_id]:
                    del self._listeners[user_id]
                    with contextlib.suppress(Exception):
                        await self._pubsub.unsubscribe(channel(user_id))  # type: ignore[union-attr]

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
