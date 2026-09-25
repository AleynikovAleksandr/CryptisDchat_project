"""Отправка push-уведомлений (APNs / FCM), Gunicorn_Celery.md 4.6.

Сервер не видит текст сообщений: в push попадает только безопасный фрагмент,
заранее переданный клиентом, или нейтральное «New message».
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PushMessage:
    token: str
    platform: str
    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)


class IPushSender(ABC):
    @abstractmethod
    def send(self, message: PushMessage) -> bool:
        """Вернуть True при успешной отправке; False — токен недействителен."""
