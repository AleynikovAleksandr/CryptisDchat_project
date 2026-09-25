"""Очередь `keys` — обслуживание резервного копирования ключей (ТЗ 6.6, Gunicorn_Celery.md 4.5).

Воркер лишь ретранслирует запечатанные блобы между клиентом и realm-сервисами:
доли секрета зашифрованы клиентом под публичный ключ конкретного realm, ответы realm —
под эфемерный ключ клиента. Очередь изолирована от `blockchain`: зависшая транзакция
не должна блокировать восстановление доступа пользователя к переписке.
"""
from __future__ import annotations

import json
import logging

from celery.signals import worker_ready
from sqlalchemy import select

from app.models import KeyBackup, KeyBackupShare, KeyRecoveryRequest, utcnow
from app.services.key_service import REALM_KEYS_REDIS
from worker.celery_app import celery_app, retry_delay
from worker.runtime import cache_redis, db, realm_clients

log = logging.getLogger(__name__)


@celery_app.task(name="keys.refresh_realm_keys")
def refresh_realm_keys() -> int:
    realms = []
    for index, client in enumerate(realm_clients()):
        try:
            realms.append({"index": index, "public_key": client.public_key()})
        except Exception as exc:  # noqa: BLE001
            log.warning("realm %d unreachable: %s", index, exc)
    if len(realms) == len(realm_clients()):
        cache_redis().set(REALM_KEYS_REDIS, json.dumps(realms), ex=3600)
    return len(realms)


@worker_ready.connect
def _publish_realm_keys_on_start(**_: object) -> None:
    """Ключи realm нужны клиентам для бэкапа сразу после запуска, не через 10 минут по расписанию."""
    refresh_realm_keys.delay()


@celery_app.task(name="keys.store_shares", bind=True, acks_late=True, max_retries=5)
def store_shares(self, user_id: str, version: int, shares: list[dict]) -> str:
    clients = realm_clients()
    failed: list[dict] = []
    with db() as session:
        backup = session.get(KeyBackup, user_id)
        if backup is None or backup.version != version:
            return "superseded"  # пришла более новая резервная копия
        for share in shares:
            idx = share["realm_index"]
            row = session.get(KeyBackupShare, (user_id, idx))
            try:
                clients[idx].store(user_id, version, share["sealed"])
                if row:
                    row.status, row.last_error = "stored", None
            except Exception as exc:  # noqa: BLE001
                failed.append(share)
                if row:
                    row.status, row.last_error = "failed", str(exc)[:255]
        rows = list(session.scalars(select(KeyBackupShare).where(KeyBackupShare.user_id == user_id)))
        if rows and all(r.status == "stored" for r in rows):
            backup.status = "active"
        session.commit()
    if failed:
        raise self.retry(args=[user_id, version, failed], countdown=retry_delay(self.request.retries))
    return "stored"


@celery_app.task(name="keys.recover", acks_late=True)
def recover(request_id: str, requests: list[dict]) -> str:
    clients = realm_clients()
    with db() as session:
        req = session.get(KeyRecoveryRequest, request_id)
        backup = session.get(KeyBackup, req.user_id) if req else None
        if req is None or req.status != "pending" or backup is None:
            return "skipped"
        shares: list[dict] = []
        attempts: list[int] = []
        destroyed = False
        for item in requests:
            idx = item["realm_index"]
            if idx >= len(clients):
                continue
            try:
                res = clients[idx].recover(req.user_id, item["sealed_request"])
            except Exception as exc:  # noqa: BLE001
                log.warning("realm %d recover failed: %s", idx, exc)
                continue
            if res.status == "ok" and res.sealed_share:
                shares.append({"realm_index": idx, "sealed_share": res.sealed_share})
            elif res.status == "destroyed":
                destroyed = True
            elif res.status == "wrong_pin" and res.attempts_left is not None:
                attempts.append(res.attempts_left)

        req.finished_at = utcnow()
        if len(shares) >= backup.threshold:
            req.status = "done"
            req.result_json = json.dumps(shares)
        elif destroyed or (attempts and min(attempts) <= 0):
            # лимит неверных попыток исчерпан — уничтожаем доли на ВСЕХ realm (4.5)
            req.status = "destroyed"
            backup.status = "destroyed"
            session.commit()
            destroy_all.delay(req.user_id)
            return req.status
        elif attempts:
            req.status = "wrong_pin"
            req.attempts_left = min(attempts)
        else:
            req.status = "failed"
        session.commit()
        return req.status


@celery_app.task(name="keys.destroy_all", bind=True, acks_late=True, max_retries=10)
def destroy_all(self, user_id: str) -> None:
    errors = 0
    for client in realm_clients():
        try:
            client.destroy(user_id)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            log.error("realm destroy failed for %s: %s", user_id, exc)
    with db() as session:
        for row in session.scalars(select(KeyBackupShare).where(KeyBackupShare.user_id == user_id)):
            row.status = "destroyed"
        session.commit()
    if errors:
        raise self.retry(countdown=retry_delay(self.request.retries))
