"""Общая декларативная база моделей.

Одна и та же `Base` подключается:
  * в FastAPI — через асинхронный движок (asyncmy);
  * во Flask-админке — через Flask-SQLAlchemy (`SQLAlchemy(model_class=Base)`);
  * в Celery — через синхронный движок (pymysql).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Время в UTC без tzinfo — так оно хранится в DATETIME(6) MariaDB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """UUID-ключи хранятся как CHAR(36) ASCII: переносимо между MariaDB и SQLite (тесты)."""

    metadata = MetaData(naming_convention=NAMING)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
