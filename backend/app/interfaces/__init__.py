"""Абстрактные интерфейсы и протоколы.

Бизнес-логика (services) зависит только от этих абстракций, а конкретные
реализации (Redis, TON, файловая система, realm-сервисы, push-провайдеры)
подставляются при сборке приложения. Это позволяет подменять инфраструктуру
в тестах и в dev-режиме (например, mock-блокчейн вместо сети TON).
"""
from .blockchain import AnchorResult, ConfirmationResult, IBlockchainGateway
from .events import IEventBus
from .push import IPushSender, PushMessage
from .rate_limit import IRateLimiter, RateLimitResult
from .realm import IRealmClient, RealmRecoverResult
from .storage import IFileStorage
from .tasks import ITaskQueue
from .ton_proof import ITonProofVerifier, TonProofRequest, VerifiedWallet

__all__ = [
    "AnchorResult", "ConfirmationResult", "IBlockchainGateway", "IEventBus", "IFileStorage",
    "IPushSender", "IRateLimiter", "IRealmClient", "ITaskQueue", "ITonProofVerifier",
    "PushMessage", "RateLimitResult", "RealmRecoverResult", "TonProofRequest", "VerifiedWallet",
]
