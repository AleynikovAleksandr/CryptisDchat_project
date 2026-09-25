"""Доменные ошибки. Обработчик в main.py превращает их в JSON {"error": code, "detail": ...}."""
from __future__ import annotations


class DomainError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, detail: str = "", *, code: str | None = None, extra: dict | None = None) -> None:
        super().__init__(detail or self.code)
        self.detail = detail or self.code
        if code:
            self.code = code
        self.extra = extra or {}


class NotFound(DomainError):
    status = 404
    code = "not_found"


class Forbidden(DomainError):
    status = 403
    code = "forbidden"


class Conflict(DomainError):
    status = 409
    code = "conflict"


class Unauthorized(DomainError):
    status = 401
    code = "unauthorized"


class Invalid(DomainError):
    status = 422
    code = "invalid"


class TooManyRequests(DomainError):
    status = 429
    code = "rate_limited"
