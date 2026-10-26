"""Очередь `keys` — обслуживание резервного копирования ключей (ТЗ 6.6, Gunicorn_Celery.md 4.5).

Воркер лишь ретранслирует запечатанные блобы между клиентом и realm-сервисами:
доли секрета зашифрованы клиентом под публичный ключ конкретного realm, ответы realm —
под эфемерный ключ клиента. Очередь изолирована от `blockchain`: зависшая транзакция
не должна блокировать восстановление доступа пользователя к переписке.
"""
from __future__ import annotations

import json
import logging
import math
import time

from celery.signals import worker_ready
from sqlalchemy import select

from app.config import get_settings
from app.models import KeyBackup, KeyBackupShare, KeyRecoveryRequest, utcnow
from app.services.key_service import REALM_KEYS_REDIS
from worker.celery_app import celery_app, retry_delay
from worker.runtime import cache_redis, db, realm_clients

log = logging.getLogger(__name__)
LOCK_SECONDS = 15 * 60


def _record_lock(req: KeyRecoveryRequest, until: float) -> None:
    req.status = "locked"
    req.attempts_left = 0
    req.result_json = json.dumps({"locked_until": until})
    req.finished_at = utcnow()


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
        redis = cache_redis()
        failures_key = f"keys:recovery:failures:{req.user_id}:{backup.version}"
        lock_key = f"keys:recovery:locked:{req.user_id}:{backup.version}"
        active_lock = redis.get(lock_key)
        if active_lock:
            until = float(active_lock)
            if until > time.time():
                _record_lock(req, until)
                session.commit()
                return req.status
            redis.delete(lock_key, failures_key)
        shares: list[dict] = []
        attempts: list[int] = []
        locked_until: list[float] = []
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
            elif res.status == "locked" and res.locked_until is not None:
                locked_until.append(res.locked_until)
            elif res.status == "wrong_pin" and res.attempts_left is not None:
                attempts.append(res.attempts_left)

        req.finished_at = utcnow()
        if locked_until:
            until = max(max(locked_until), time.time() + LOCK_SECONDS)
            redis.set(lock_key, until, ex=math.ceil(until - time.time()))
            redis.delete(failures_key)
            _record_lock(req, until)
        elif len(shares) >= backup.threshold:
            redis.delete(lock_key, failures_key)
            req.status = "done"
            req.result_json = json.dumps(shares)
        elif attempts:
            count = redis.incr(failures_key)
            if count >= get_settings().backup_max_pin_attempts:
                until = time.time() + LOCK_SECONDS
                redis.set(lock_key, until, ex=LOCK_SECONDS)
                redis.delete(failures_key)
                _record_lock(req, until)
            else:
                req.status = "wrong_pin"
                req.attempts_left = get_settings().backup_max_pin_attempts - count
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
