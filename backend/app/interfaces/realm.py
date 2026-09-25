"""Клиент сервиса восстановления ключей («realm», ТЗ 6.6).

Все полезные нагрузки запечатаны клиентом ECIES под публичный ключ realm —
ни backend, ни воркер не видят ни долей секрета, ни PIN-производных.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RealmRecoverResult:
    status: str                    # ok | wrong_pin | destroyed | not_found | error
    sealed_share: str | None = None  # доля под эфемерный ключ клиента
    attempts_left: int | None = None


class IRealmClient(ABC):
    @abstractmethod
    def public_key(self) -> str:
        """SPKI (base64) ключа realm, под который клиент запечатывает данные."""

    @abstractmethod
    def store(self, user_id: str, version: int, sealed: str) -> None:
        """Сохранить запечатанную долю секрета (перезаписывает предыдущую версию)."""

    @abstractmethod
    def recover(self, user_id: str, sealed_request: str) -> RealmRecoverResult:
        """Проверить PIN-производный ключ доступа и вернуть долю или ошибку."""

    @abstractmethod
    def destroy(self, user_id: str) -> None:
        """Необратимо уничтожить долю пользователя."""
