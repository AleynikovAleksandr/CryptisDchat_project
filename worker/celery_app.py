"""Celery-приложение CryptisDchat (Gunicorn_Celery.md, раздел 4).

Очереди (4.1): blockchain, keys, notifications, media, maintenance.
Запуск (4.9) — два сервиса воркера из одного образа, чтобы медленная блокчейн-задача
не занимала слоты быстрых push-уведомлений:
    celery -A worker.celery_app worker -Q blockchain,keys --concurrency=2
    celery -A worker.celery_app worker -Q notifications,media,maintenance --concurrency=4
    celery -A worker.celery_app beat     (строго в одном экземпляре)
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from app.config import get_settings
from app.services.task_queue import QUEUES, TASK_ROUTES
from utils import setup_logging

settings = get_settings()
setup_logging()

celery_app = Celery(
    "cryptis",
    broker=settings.redis_url(settings.redis_db_broker),
    backend=settings.redis_url(settings.redis_db_results),
    include=[
        "worker.tasks.blockchain",
        "worker.tasks.keys",
        "worker.tasks.notifications",
        "worker.tasks.media",
        "worker.tasks.maintenance",
    ],
)

celery_app.conf.update(
    task_queues=[Queue(name) for name in QUEUES],
    task_routes=TASK_ROUTES,
    task_default_queue="maintenance",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,
    timezone="UTC",
    enable_utc=True,
    # 4.8: подтверждение брокеру только после выполнения; при гибели воркера — вернуть в очередь
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 3600},
    broker_connection_retry_on_startup=True,
    beat_schedule={
        # 4.2: «раз в 1–5 минут или как только накопилось N записей» (N — триггер в API)
        "chain-flush": {"task": "blockchain.flush_batch", "schedule": float(settings.chain_batch_interval_seconds)},
        "chain-recheck": {"task": "blockchain.recheck_submitted", "schedule": 120.0},
        "chain-sweep": {"task": "blockchain.sweep_orphans", "schedule": 600.0},
        "realm-keys": {"task": "keys.refresh_realm_keys", "schedule": 600.0},
        "media-retention": {"task": "media.purge_by_retention", "schedule": crontab(minute=15)},
        "messages-expire": {"task": "maintenance.purge_expired_messages", "schedule": 60.0},
        "unread-reconcile": {"task": "maintenance.reconcile_unread", "schedule": 600.0},
        "tokens-cleanup": {"task": "maintenance.cleanup_tokens", "schedule": crontab(hour=3, minute=0)},
        "db-backup": {"task": "maintenance.backup_database", "schedule": crontab(hour=4, minute=0)},
    },
)

# Ретраи критичных очередей (4.8): 5 попыток, 30с → 1м → 2м → 5м → 10м, затем — ручной разбор в админке
RETRY_DELAYS = [30, 60, 120, 300, 600]


def retry_delay(attempt: int) -> int:
    return RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
