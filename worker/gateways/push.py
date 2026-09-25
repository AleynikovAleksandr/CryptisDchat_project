"""Отправка push-уведомлений.

`LogPushSender` — реализация по умолчанию: пишет уведомление в лог воркера.
Для APNs/FCM нужны учётные данные провайдеров; их реализация подключается через тот же
интерфейс IPushSender без изменений в задачах (worker/tasks/notifications.py).
"""
from __future__ import annotations

import logging

from app.interfaces.push import IPushSender, PushMessage

log = logging.getLogger("cryptis.push")


class LogPushSender(IPushSender):
    def send(self, message: PushMessage) -> bool:
        log.info("push → %s[%s…]: %s — %s", message.platform, message.token[:8], message.title, message.body)
        return True
