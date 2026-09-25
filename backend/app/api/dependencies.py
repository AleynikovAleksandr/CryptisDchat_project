"""Зависимости FastAPI: сессия БД, инфраструктура, текущий пользователь, rate limiting."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories.database import session_scope
from app.services.auth_service import AuthService
from app.services.errors import TooManyRequests, Unauthorized
from app.services.infra import Infra
from app.services.rate_limiter import parse_limit


def get_infra(request: Request) -> Infra:
    return request.app.state.infra


async def get_session() -> AsyncIterator[AsyncSession]:
    async for session in session_scope():
        yield session


@dataclass
class CurrentUser:
    user: User
    device_id: str


def client_ip(request: Request) -> str:
    # за reverse-proxy реальный IP приходит в X-Forwarded-For (Gunicorn forwarded_allow_ips)
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
    infra: Infra = Depends(get_infra),
) -> CurrentUser:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    user, device_id = await AuthService(session, infra).authenticate(header[7:].strip())
    request.state.user_id = user.id
    await _rate_limit(infra, f"{user.id}:api", infra.settings.rate_limit_default)
    return CurrentUser(user=user, device_id=device_id)


async def _rate_limit(infra: Infra, key: str, spec: str) -> None:
    limit, window = parse_limit(spec)
    result = await infra.limiter.hit(key, limit, window)
    if not result.allowed:
        raise TooManyRequests("too many requests", extra={"retry_after": result.retry_after})


async def auth_rate_limit(request: Request, infra: Infra = Depends(get_infra)) -> None:
    """Для неаутентифицированных эндпоинтов входа — лимит по IP."""
    await _rate_limit(infra, f"{client_ip(request)}:auth", infra.settings.rate_limit_auth)
