"""Тестовое окружение: SQLite (вместо MariaDB), fakeredis (вместо Redis), Celery в eager-режиме,
три настоящих realm-сервиса в памяти процесса, мгновенный «блокчейн»."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

_TMP = Path(tempfile.mkdtemp(prefix="cryptis-tests-"))
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_URL": f"sqlite+aiosqlite:///{_TMP}/test.db",
    "SYNC_DATABASE_URL": f"sqlite:///{_TMP}/test.db",
    "JWT_SECRET": "test-jwt-secret-0123456789abcdef0123456789",
    "TON_PROOF_SECRET": "test-proof-secret",
    "TON_PROOF_DOMAIN": "testserver",
    "DEV_WALLET_LOGIN": "true",
    "UPLOADS_DIR": str(_TMP / "uploads"),
    "ALLOWED_WS_ORIGINS": "http://testserver",
    "RATE_LIMIT_DEFAULT": "10000/60",
    "RATE_LIMIT_AUTH": "10000/60",
    "CHAIN_BATCH_MAX_ITEMS": "500",
    "WEBHOOK_SECRET": "hook-secret",
    "ADMIN_SECRET_KEY": "admin-secret",
})

import hashlib  # noqa: E402

import fakeredis  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.interfaces import IRealmClient, ITaskQueue, RealmRecoverResult  # noqa: E402
from app.interfaces.blockchain import AnchorResult, ConfirmationResult, IBlockchainGateway  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.repositories import database  # noqa: E402
from app.services.event_bus import RedisEventBus  # noqa: E402
from app.services.infra import Infra  # noqa: E402
from app.services.rate_limiter import RedisRateLimiter  # noqa: E402
from app.services.storage import LocalFileStorage  # noqa: E402
from app.services.ton_proof import TonProofVerifier  # noqa: E402
from realm.app import create_app as create_realm_app  # noqa: E402
from tests import client_crypto as cc  # noqa: E402
from worker import runtime  # noqa: E402
from worker.celery_app import celery_app  # noqa: E402

celery_app.loader.import_default_modules()  # регистрирует задачи из include=[...]
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


class InstantChain(IBlockchainGateway):
    """Сеть, где транзакция подтверждается сразу; считает отправки, чтобы проверять идемпотентность."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_next = 0

    def anchor(self, comment: str, idempotency_key: str) -> AnchorResult:
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("node unavailable")
        self.sent.append(comment)
        return AnchorResult(tx_ref=f"ref:{idempotency_key}", submitted=True)

    def check(self, tx_ref: str) -> ConfirmationResult:
        return ConfirmationResult(confirmed=True, tx_hash=hashlib.sha256(tx_ref.encode()).hexdigest(), lt=1)


class TestClientRealm(IRealmClient):
    def __init__(self, client: TestClient) -> None:
        self.c = client

    def public_key(self) -> str:
        return self.c.get("/v1/public-key").json()["public_key"]

    def store(self, user_id: str, version: int, sealed: str) -> None:
        self.c.put(f"/v1/shares/{user_id}", json={"version": version, "sealed": sealed}).raise_for_status()

    def recover(self, user_id: str, sealed_request: str) -> RealmRecoverResult:
        r = self.c.post(f"/v1/shares/{user_id}/recover", json={"sealed_request": sealed_request})
        if r.status_code == 404:
            return RealmRecoverResult(status="not_found")
        body = r.json()
        return RealmRecoverResult(status=body["status"], sealed_share=body.get("sealed_share"),
                                  attempts_left=body.get("attempts_left"))

    def destroy(self, user_id: str) -> None:
        self.c.delete(f"/v1/shares/{user_id}")


class EagerTasks(ITaskQueue):
    """Задачи выполняются сразу — настоящим кодом воркера, через Celery в eager-режиме."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def enqueue(self, task_name: str, *args, countdown=None, **kwargs) -> str:
        self.calls.append((task_name, args, kwargs))
        return celery_app.tasks[task_name].apply(args=args, kwargs=kwargs, throw=True).id

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class PushRecorder:
    def __init__(self) -> None:
        self.sent = []

    def send(self, message) -> bool:
        self.sent.append(message)
        return True


@pytest.fixture(autouse=True)
def fresh_db():
    get_settings.cache_clear()
    engine = create_engine(os.environ["SYNC_DATABASE_URL"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()
    runtime._sessionmaker.cache_clear()
    yield


@pytest.fixture
def redis_server():
    return fakeredis.FakeServer()


@pytest.fixture
def chain():
    return InstantChain()


@pytest.fixture
def push():
    return PushRecorder()


@pytest.fixture
def realms(tmp_path):
    clients = []
    for i in range(3):
        app = create_realm_app(data_dir=tmp_path / f"realm{i}", api_token="t", max_attempts=10)
        tc = TestClient(app, headers={"Authorization": "Bearer t"})
        clients.append(TestClientRealm(tc))
    return clients


@pytest.fixture
def tasks():
    return EagerTasks()


@pytest_asyncio.fixture
async def infra(redis_server, chain, realms, tasks, push):
    s = get_settings()
    cache = fakeredis.aioredis.FakeRedis(server=redis_server, decode_responses=True)
    pubsub = fakeredis.aioredis.FakeRedis(server=redis_server, decode_responses=True)
    sync_redis = fakeredis.FakeRedis(server=redis_server, decode_responses=True)
    runtime.override("cache_redis", sync_redis)
    runtime.override("pubsub_redis", sync_redis)
    runtime.override("blockchain", chain)
    runtime.override("realms", realms)
    runtime.override("push", push)
    inf = Infra(
        settings=s, redis=cache, bus=RedisEventBus(pubsub), tasks=tasks,
        storage=LocalFileStorage(s.uploads_dir), limiter=RedisRateLimiter(cache), ton_verifier=TonProofVerifier(s),
    )
    inf.sync_redis = sync_redis  # type: ignore[attr-defined]
    yield inf
    await inf.bus.close()
    await database.dispose_engine()
    runtime._overrides.clear()


@pytest_asyncio.fixture
async def app(infra):
    application = create_app(infra)
    yield application


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


class User:
    """Тестовый пользователь: dev-кошелёк, ключи устройства, токены."""

    def __init__(self, client: AsyncClient, data: dict, wallet: cc.DevWallet) -> None:
        self.client = client
        self.id = data["user"]["id"]
        self.access = data["access_token"]
        self.refresh = data["refresh_token"]
        self.wallet = wallet
        self.keys = cc.DeviceKeys()
        self.me = data["user"]

    @property
    def h(self) -> dict:
        return {"Authorization": f"Bearer {self.access}"}

    async def get(self, url: str, **kw):
        return await self.client.get(url, headers=self.h, **kw)

    async def post(self, url: str, json=None, **kw):
        return await self.client.post(url, json=json, headers={**self.h, **kw.pop("headers", {})}, **kw)

    async def patch(self, url: str, json=None):
        return await self.client.patch(url, json=json, headers=self.h)

    async def delete(self, url: str):
        return await self.client.delete(url, headers=self.h)


async def login(client: AsyncClient, wallet: cc.DevWallet | None = None, device_cookie: str | None = None) -> dict:
    """Вход кошельком. Без device_cookie — как из нового браузера; `_device` — выданная cookie устройства."""
    wallet = wallet or cc.DevWallet()
    ch = (await client.post("/api/auth/ton-proof/challenge")).json()
    client.cookies.clear()
    if device_cookie:
        client.cookies.set("cx_device", device_cookie, path="/api/auth")
    r = await client.post("/api/auth/ton-proof/verify", json=wallet.proof(ch["payload"], ch["domain"]))
    client.cookies.clear()
    assert r.status_code == 200, r.text
    return {**r.json(), "_wallet": wallet, "_device": r.cookies.get("cx_device"), "_set_cookie": r.headers["set-cookie"]}


@pytest.fixture
def make_user(client):
    async def _make(name: str | None = None, with_keys: bool = True) -> User:
        data = await login(client)
        u = User(client, data, data["_wallet"])
        if name:
            assert (await u.patch("/api/me", {"display_name": name})).status_code == 200
        if with_keys:
            r = await u.post("/api/keys", u.keys.public())
            assert r.status_code == 201, r.text
        return u
    return _make
