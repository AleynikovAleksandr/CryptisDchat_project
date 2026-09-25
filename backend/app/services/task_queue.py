"""Отправка задач в Celery по имени (API-процесс не импортирует код воркера).

Маршруты по очередям (Gunicorn_Celery.md 4.1) общие для отправителя и воркера.
"""
from __future__ import annotations

from typing import Any

from celery import Celery

from app.config import Settings
from app.interfaces.tasks import ITaskQueue

TASK_ROUTES: dict[str, dict[str, str]] = {
    "blockchain.*": {"queue": "blockchain"},
    "keys.*": {"queue": "keys"},
    "notifications.*": {"queue": "notifications"},
    "media.*": {"queue": "media"},
    "maintenance.*": {"queue": "maintenance"},
}

QUEUES = ("blockchain", "keys", "notifications", "media", "maintenance")


def make_producer(settings: Settings) -> Celery:
    app = Celery(
        "cryptis-producer",
        broker=settings.redis_url(settings.redis_db_broker),
        backend=settings.redis_url(settings.redis_db_results),
    )
    app.conf.task_routes = TASK_ROUTES
    app.conf.task_default_queue = "maintenance"
    return app


class CeleryTaskQueue(ITaskQueue):
    def __init__(self, celery_app: Celery) -> None:
        self.celery = celery_app

    def enqueue(self, task_name: str, *args: Any, countdown: int | None = None, **kwargs: Any) -> str:
        result = self.celery.send_task(task_name, args=args, kwargs=kwargs, countdown=countdown)
        return result.id
