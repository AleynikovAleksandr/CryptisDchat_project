"""Асинхронный движок SQLAlchemy для FastAPI (asyncmy → MariaDB).

Ports_and_Database.md, 3.2: в async-обработчиках нельзя использовать синхронный
PyMySQL — он блокирует event loop. Движок создаётся лениво в каждом воркере
Gunicorn после форка (preload_app = False), поэтому соединения не шарятся между процессами.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        kwargs: dict = {"pool_pre_ping": True}
        if not settings.async_db_url.startswith("sqlite"):
            kwargs.update(pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow, pool_recycle=1800)
        _engine = create_async_engine(settings.async_db_url, **kwargs)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def session_scope() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: одна сессия на запрос, коммит делают сервисы явно."""
    async with get_sessionmaker()() as session:
        yield session


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
