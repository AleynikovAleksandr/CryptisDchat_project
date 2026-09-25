"""Шина событий реального времени (Redis Pub/Sub, Gunicorn_Celery.md 3.1–3.2).

Pub/Sub — лишь «звоночек» уже подключённому клиенту; источник истины — БД.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any


class IEventBus(ABC):
    @abstractmethod
    async def publish(self, user_id: str, event: str, data: dict[str, Any]) -> None:
        """Опубликовать событие в канал пользователя `events:{user_id}`."""

    @abstractmethod
    def subscribe(self, user_id: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Асинхронный поток событий пользователя: (имя события, данные)."""
