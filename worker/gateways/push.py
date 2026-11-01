"""Отправка push-уведомлений.

`WebPushSender` отправляет уведомления браузерам (Web Push, RFC 8030/8291/8292), если задан
VAPID_PRIVATE_KEY. Остальные платформы (APNs/FCM) и режим без ключа обслуживает `LogPushSender`:
он пишет уведомление в лог воркера. Задачи (worker/tasks/notifications.py) от выбора не зависят.
"""
from __future__ import annotations

import json
import logging

from cryptography.hazmat.primitives import serialization
from pywebpush import WebPushException, webpush

from app.interfaces.push import IPushSender, PushMessage
from app.services import webpush as webpush_service

log = logging.getLogger("cryptis.push")

PUSH_TTL_SECONDS = 24 * 3600


class LogPushSender(IPushSender):
    def send(self, message: PushMessage) -> bool:
        log.info("push → %s[%s…]: %s — %s", message.platform, message.token[:8], message.title, message.body)
        return True


class WebPushSender(IPushSender):
    def __init__(self, vapid_private_key: str, subject: str, fallback: IPushSender) -> None:
        # Ключ хранится в прежнем формате base64url (32-байтовый скаляр P-256).
        # pywebpush принимает PEM, поэтому конвертируем его один раз при создании sender.
        private_key = webpush_service.load_vapid_key(vapid_private_key)
        self.pem_key = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        self.key = vapid_private_key
        self.subject = subject
        self.fallback = fallback

    def send(self, message: PushMessage) -> bool:
        if message.platform != "webpush":
            return self.fallback.send(message)
        try:
            sub = webpush_service.parse_subscription(message.token)
        except ValueError:
            return False  # подписка негодная — токен удалится
        payload = json.dumps({"title": message.title, "body": message.body, **message.data},
                             separators=(",", ":"), ensure_ascii=False).encode()
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": webpush_service.b64u(sub.p256dh), "auth": webpush_service.b64u(sub.auth)},
                },
                data=payload.decode("utf-8"),
                vapid_private_key=self.pem_key,
                vapid_claims={"sub": self.subject},
                ttl=PUSH_TTL_SECONDS,
                timeout=10,
                headers={"Urgency": "high"},
            )
        except WebPushException as e:
            response = getattr(e, "response", None)
            if response is not None and response.status_code in (404, 410):
                return False  # подписка отозвана браузером
            log.warning("web push to %s failed: %s", sub.endpoint[:60], e)
            return True  # сеть — не повод удалять подписку
        except Exception as e:
            log.warning("web push to %s failed: %s", sub.endpoint[:60], e)
        return True
