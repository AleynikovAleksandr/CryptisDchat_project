"""JWT (access) и Refresh-токены (ТЗ 4.2).

* access — короткоживущий JWT (15 мин), `sub` = UUID пользователя, `did` = UUID устройства;
* refresh — случайный UUID4, в БД хранится только его SHA-256;
* отзыв: JWT, выданный раньше `users.sessions_revoked_at`, `devices.revoked_at`
  или `devices.tokens_revoked_at`, отклоняется даже до истечения срока;
* device_key — ключ браузера, выдаётся сервером в HttpOnly-cookie и меняется при каждом входе;
  в БД хранится только его SHA-256.
"""
from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import jwt

from app.config import get_settings


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class AccessClaims:
    user_id: str
    device_id: str
    issued_at: float  # unix, с долями секунды
    expires_at: int


def create_access_token(user_id: str, device_id: str) -> tuple[str, int]:
    s = get_settings()
    now = time.time()
    exp = int(now) + s.access_token_ttl_seconds
    payload = {
        "sub": user_id,
        "did": device_id,
        "iat": int(now),
        "iat_ms": int(now * 1000),  # точность до мс, чтобы отзыв «сейчас» работал внутри той же секунды
        "exp": exp,
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), exp


def decode_access_token(token: str) -> AccessClaims:
    s = get_settings()
    try:
        payload = jwt.decode(
            token, s.jwt_secret, algorithms=[s.jwt_algorithm], options={"require": ["sub", "exp", "iat"]}
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("typ") != "access" or "did" not in payload:
        raise TokenError("wrong token type")
    return AccessClaims(
        user_id=payload["sub"],
        device_id=payload["did"],
        issued_at=payload.get("iat_ms", payload["iat"] * 1000) / 1000,
        expires_at=payload["exp"],
    )


def issued_before(claims: AccessClaims, revoked_at: datetime | None) -> bool:
    """True, если токен выдан до момента отзыва (naive UTC в БД)."""
    if revoked_at is None:
        return False
    revoked_ts = revoked_at.replace(tzinfo=timezone.utc).timestamp()
    return claims.issued_at <= revoked_ts


def new_refresh_token() -> str:
    return str(uuid.uuid4())


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def new_device_key() -> str:
    return secrets.token_urlsafe(32)


def hash_device_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
