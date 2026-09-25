"""Набор инфраструктурных зависимостей, собираемый один раз на процесс (lifespan FastAPI)."""
from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.config import Settings
from app.interfaces import IEventBus, IFileStorage, IRateLimiter, ITaskQueue, ITonProofVerifier


@dataclass
class Infra:
    settings: Settings
    redis: Redis              # DB 0 — кэш горячих данных, presence, rate limit, одноразовые payload
    bus: IEventBus            # Pub/Sub (DB 3)
    tasks: ITaskQueue         # Celery (брокер — DB 1)
    storage: IFileStorage
    limiter: IRateLimiter
    ton_verifier: ITonProofVerifier
