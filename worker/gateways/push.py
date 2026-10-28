"""Отправка push-уведомлений.

`WebPushSender` отправляет уведомления браузерам (Web Push, RFC 8030/8291/8292), если задан
VAPID_PRIVATE_KEY. Остальные платформы (APNs/FCM) и режим без ключа обслуживает `LogPushSender`:
он пишет уведомление в лог воркера. Задачи (worker/tasks/notifications.py) от выбора не зависят.
"""
from __future__ import annotations

import json
import logging

import httpx

from app.interfaces.push import IPushSender, PushMessage
from app.services import webpush

log = logging.getLogger("cryptis.push")

PUSH_TTL_SECONDS = 24 * 3600


class LogPushSender(IPushSender):
    def send(self, message: PushMessage) -> bool:
        log.info("push → %s[%s…]: %s — %s", message.platform, message.token[:8], message.title, message.body)
        return True


class WebPushSender(IPushSender):
    def __init__(self, vapid_private_key: str, subject: str, fallback: IPushSender,
                 client: httpx.Client | None = None) -> None:
        webpush.load_vapid_key(vapid_private_key)  # неверный ключ — ошибка сразу, а не на первом push
        self.key = vapid_private_key
        self.subject = subject
        self.fallback = fallback
        self.client = client or httpx.Client(timeout=10, follow_redirects=False)

    def send(self, message: PushMessage) -> bool:
        if message.platform != "webpush":
            return self.fallback.send(message)
        try:
            sub = webpush.parse_subscription(message.token)
        except ValueError:
            return False  # подписка негодная — токен удалится
        payload = json.dumps({"title": message.title, "body": message.body, **message.data},
                             separators=(",", ":"), ensure_ascii=False).encode()
        headers = {
            "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
            "TTL": str(PUSH_TTL_SECONDS), "Urgency": "high",
            **webpush.vapid_headers(sub.endpoint, self.key, self.subject),
        }
        try:
            r = self.client.post(sub.endpoint, content=webpush.encrypt(sub, payload), headers=headers)
        except httpx.HTTPError as e:
            log.warning("web push to %s failed: %s", sub.endpoint[:60], e)
            return True  # сеть — не повод удалять подписку
        if r.status_code in (404, 410):  # подписка отозвана браузером
            return False
        if r.status_code >= 400:
            log.warning("web push to %s rejected: %s %s", sub.endpoint[:60], r.status_code, r.text[:200])
        return True
