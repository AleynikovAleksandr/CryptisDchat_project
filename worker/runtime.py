"""Общие ресурсы процесса воркера: синхронная БД, Redis, шлюзы. Создаются лениво после форка."""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.interfaces import IBlockchainGateway, IPushSender, IRealmClient
from app.services.event_bus import channel, encode_event


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    s = get_settings()
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if not s.sync_db_url.startswith("sqlite"):
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=1800)
    return sessionmaker(create_engine(s.sync_db_url, **kwargs), expire_on_commit=False)


@contextmanager
def db() -> Iterator[Session]:
    session = _sessionmaker()()
    try:
        yield session
    finally:
        session.close()


# ------------------------------------------------------------------ подмены (используются тестами)
_overrides: dict[str, Any] = {}


def override(name: str, value: Any) -> None:
    _overrides[name] = value


def cache_redis() -> redis.Redis:
    return _overrides.get("cache_redis") or _cache_redis()


def pubsub_redis() -> redis.Redis:
    return _overrides.get("pubsub_redis") or _pubsub_redis()


@lru_cache
def _cache_redis() -> redis.Redis:
    s = get_settings()
    return redis.Redis.from_url(s.redis_url(s.redis_db_cache), decode_responses=True)


@lru_cache
def _pubsub_redis() -> redis.Redis:
    s = get_settings()
    return redis.Redis.from_url(s.redis_url(s.redis_db_pubsub), decode_responses=True)


def publish(user_id: str, event: str, data: dict[str, Any]) -> None:
    """Тот же формат, что у RedisEventBus в API: воркер Gunicorn доставит событие в сокет."""
    pubsub_redis().publish(channel(user_id), encode_event(event, data))


def publish_many(user_ids: list[str], event: str, data: dict[str, Any]) -> None:
    payload = encode_event(event, data)
    pipe = pubsub_redis().pipeline()
    for uid in user_ids:
        pipe.publish(channel(uid), payload)
    pipe.execute()


# ------------------------------------------------------------------ шлюзы
def blockchain() -> IBlockchainGateway:
    if "blockchain" in _overrides:
        return _overrides["blockchain"]
    return _blockchain()


@lru_cache
def _blockchain() -> IBlockchainGateway:
    s = get_settings()
    if s.blockchain_mode == "toncenter":
        from worker.gateways.ton import ToncenterGateway

        return ToncenterGateway(s)
    from worker.gateways.ton import MockBlockchainGateway

    return MockBlockchainGateway(cache_redis())


def realm_clients() -> list[IRealmClient]:
    if "realms" in _overrides:
        return _overrides["realms"]
    from worker.gateways.realm import HttpRealmClient

    s = get_settings()
    return [HttpRealmClient(url, s.realm_api_token) for url in s.realm_url_list]


def push_sender() -> IPushSender:
    if "push" in _overrides:
        return _overrides["push"]
    from worker.gateways.push import LogPushSender

    return LogPushSender()


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))
