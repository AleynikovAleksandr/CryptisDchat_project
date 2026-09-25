"""Точка входа FastAPI (запуск: gunicorn -c gunicorn.conf.py app.main:app)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from app.api.routers import auth, groups, keys, me, messages, pages, settings, threads, ws
from app.config import get_settings
from app.repositories.database import dispose_engine, get_engine
from app.services.errors import DomainError
from app.services.event_bus import RedisEventBus
from app.services.infra import Infra
from app.services.rate_limiter import RedisRateLimiter
from app.services.storage import LocalFileStorage
from app.services.task_queue import CeleryTaskQueue, make_producer
from app.services.ton_proof import TonProofVerifier
from utils import setup_logging

log = logging.getLogger("cryptis")

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
# TonConnect UI грузится с jsDelivr и открывает мосты кошельков — разрешаем только их
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self' ws: wss: https:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def build_infra() -> Infra:
    s = get_settings()
    cache_redis = Redis.from_url(s.redis_url(s.redis_db_cache), decode_responses=True)
    pubsub_redis = Redis.from_url(s.redis_url(s.redis_db_pubsub), decode_responses=True)
    return Infra(
        settings=s,
        redis=cache_redis,
        bus=RedisEventBus(pubsub_redis),
        tasks=CeleryTaskQueue(make_producer(s)),
        storage=LocalFileStorage(s.uploads_dir),
        limiter=RedisRateLimiter(cache_redis),
        ton_verifier=TonProofVerifier(s),
    )


def create_app(infra: Infra | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # движок и соединения создаются здесь — уже ПОСЛЕ форка воркера Gunicorn (preload_app=False)
        if not hasattr(app.state, "infra"):
            app.state.infra = build_infra()
        get_engine()
        yield
        bus = app.state.infra.bus
        if hasattr(bus, "close"):
            await bus.close()
        await dispose_engine()

    s = get_settings()
    s.check_production_secrets()
    setup_logging("DEBUG" if s.app_env == "development" else "INFO")
    app = FastAPI(
        title="CryptisDchat API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs" if s.is_dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if s.is_dev else None,
    )
    if infra is not None:
        app.state.infra = infra

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        headers = {}
        if exc.status == 429 and "retry_after" in exc.extra:
            headers["Retry-After"] = str(exc.extra["retry_after"])
        return JSONResponse({"error": exc.code, "detail": exc.detail, **exc.extra}, status_code=exc.status,
                            headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return JSONResponse({"error": "invalid", "detail": f"{loc}: {first.get('msg', 'invalid request')}"},
                            status_code=422)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", CSP)
        return response

    for r in (auth.router, me.router, threads.router, groups.router, messages.router,
              settings.router, keys.router, ws.router, pages.router):
        app.include_router(r)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
