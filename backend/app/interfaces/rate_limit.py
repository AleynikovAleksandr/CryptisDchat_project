"""Ограничение частоты запросов (ТЗ 7), ключи ratelimit:{uuid|ip}:{endpoint} в Redis."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: int


class IRateLimiter(ABC):
    @abstractmethod
    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """Учесть обращение по ключу в скользящем окне и сказать, разрешено ли оно."""
