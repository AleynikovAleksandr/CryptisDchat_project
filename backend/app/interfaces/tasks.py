"""Постановка фоновых задач в очереди Celery (Gunicorn_Celery.md 4.1).

API-процесс не импортирует код воркера: задачи отправляются по имени,
а маршрутизация по очередям задана в worker/celery_app.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ITaskQueue(ABC):
    @abstractmethod
    def enqueue(self, task_name: str, *args: Any, countdown: int | None = None, **kwargs: Any) -> str:
        """Поставить задачу, вернуть её id."""
