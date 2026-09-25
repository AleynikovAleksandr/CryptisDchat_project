"""Шлюз к блокчейну TON. Используется только изолированным воркером (очередь blockchain)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AnchorResult:
    """Результат отправки транзакции-«якоря» от служебного кошелька."""

    tx_ref: str          # ссылка для последующей проверки (хэш сообщения / комментарий)
    submitted: bool


@dataclass(frozen=True)
class ConfirmationResult:
    confirmed: bool
    tx_hash: str | None = None
    lt: int | None = None
    confirmations: int = 0


class IBlockchainGateway(ABC):
    """Запись коротких доказательств (корень Меркла, хэш изменения состава) в TON."""

    @abstractmethod
    def anchor(self, comment: str, idempotency_key: str) -> AnchorResult:
        """Отправить транзакцию служебного кошелька с текстовым комментарием `comment`.

        Реализация обязана быть идемпотентной по `idempotency_key`: повтор после сбоя
        не должен порождать вторую транзакцию.
        """

    @abstractmethod
    def check(self, tx_ref: str) -> ConfirmationResult:
        """Проверить, подтверждена ли ранее отправленная транзакция."""
