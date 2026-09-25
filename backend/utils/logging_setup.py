"""Единый формат логов приложения.

Сервер не логирует ни тела запросов, ни токены, ни шифротексты — только метаданные.
Фильтр ниже дополнительно вырезает из сообщений всё, похожее на Bearer-токен.
"""
from __future__ import annotations

import logging
import re

_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")


class RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "earer" in record.msg:
            record.msg = _BEARER.sub(r"\1[redacted]", record.msg)
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_cryptis_configured", False):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecrets())
    root.addHandler(handler)
    root.setLevel(level)
    root._cryptis_configured = True  # type: ignore[attr-defined]
