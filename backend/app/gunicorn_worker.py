"""Uvicorn-воркер для Gunicorn с лимитом соединений из gunicorn.conf.py.

Штатный uvicorn.workers.UvicornWorker не передаёт в Uvicorn `worker_connections`, поэтому
лимит из конфига молча игнорировался. Здесь он становится `limit_concurrency`: сверх него
воркер отвечает 503 на новые соединения, а не исчерпывает память и файловые дескрипторы.
"""
from __future__ import annotations

from typing import Any

from uvicorn.workers import UvicornWorker


class CryptisUvicornWorker(UvicornWorker):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # протоколы читают значение при каждом новом соединении — достаточно выставить до запуска сервера
        self.config.limit_concurrency = self.cfg.worker_connections
