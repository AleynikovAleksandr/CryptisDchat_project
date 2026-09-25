"""Хранилище зашифрованных блобов (вложения, миниатюры, аватары)."""
from __future__ import annotations

from abc import ABC, abstractmethod


class IFileStorage(ABC):
    @abstractmethod
    async def save(self, key: str, data: bytes) -> str:
        """Сохранить блоб, вернуть путь хранения."""

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """Прочитать блоб целиком."""

    @abstractmethod
    def delete(self, path: str) -> None:
        """Удалить блоб (синхронно: вызывается из Celery-задач очереди media)."""
