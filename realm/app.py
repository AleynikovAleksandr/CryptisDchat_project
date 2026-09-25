"""Realm — независимый сервис хранения доли секрета (ТЗ 6.6, аналог Juicebox в XChat).

Разворачивается в трёх изолированных контейнерах (realm1..realm3) с собственными томами.
Каждый хранит ОДНУ долю ключа K (схема Шамира 2-из-3), которым клиент зашифровал
резервную копию своих приватных ключей.

Модель угроз:
  * доля и PIN-производный ключ доступа запечатаны клиентом под ключ realm (ECIES) —
    backend, брокер и воркер их не видят;
  * PIN не покидает устройство: клиент присылает auth = HMAC(PBKDF2(PIN), "realm-auth:<i>");
  * после MAX_ATTEMPTS неверных попыток доля уничтожается безвозвратно — перебор PIN
    невозможен даже при компрометации backend;
  * компрометация одного realm не раскрывает K: нужно минимум две доли.
Ключ realm хранится в файле с правами 0600 — программный эквивалент HSM на первом этапе.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

try:
    from realm import ecies
except ImportError:  # запуск внутри контейнера realm, где пакет лежит плоско
    import ecies  # type: ignore[no-redef]

DATA_DIR = Path(os.environ.get("REALM_DATA_DIR", "/data"))
API_TOKEN = os.environ.get("REALM_API_TOKEN", "")
MAX_ATTEMPTS = int(os.environ.get("REALM_MAX_ATTEMPTS", "10"))
REALM_ID = os.environ.get("REALM_ID", "realm")


class Store:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.key = self._load_key(data_dir / "realm_key.pem")
        self.db = sqlite3.connect(data_dir / "shares.db", check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS shares ("
            " user_id TEXT PRIMARY KEY, version INTEGER NOT NULL, sealed TEXT NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)"
        )
        self.db.commit()

    @staticmethod
    def _load_key(path: Path) -> ec.EllipticCurvePrivateKey:
        if path.exists():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            assert isinstance(key, ec.EllipticCurvePrivateKey)
            return key
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(pem)
        return key

    def public_key(self) -> str:
        return ecies.spki_b64(self.key.public_key())

    def open_json(self, blob: str) -> dict:
        try:
            return json.loads(ecies.open_sealed(self.key, blob))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, "cannot open sealed payload") from exc


class StoreIn(BaseModel):
    version: int = Field(ge=1)
    sealed: str = Field(max_length=4096)


class RecoverIn(BaseModel):
    sealed_request: str = Field(max_length=4096)


def create_app(data_dir: Path | None = None, api_token: str | None = None,
               max_attempts: int | None = None) -> FastAPI:
    data_dir = data_dir or DATA_DIR
    token = API_TOKEN if api_token is None else api_token
    limit = max_attempts or MAX_ATTEMPTS
    app = FastAPI(title=f"CryptisDchat {REALM_ID}", docs_url=None, redoc_url=None, openapi_url=None)
    holder: dict[str, Store] = {}

    def get_store() -> Store:
        if "store" not in holder:  # лениво: каталог данных создаётся при первом запросе
            holder["store"] = Store(data_dir)
        return holder["store"]

    def require_token(authorization: str = Header(default="")) -> None:
        if not token or not hmac.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "unauthorized")

    auth = [Depends(require_token)]

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "realm": REALM_ID}

    @app.get("/v1/public-key", dependencies=auth)
    def public_key() -> dict:
        return {"public_key": get_store().public_key()}

    @app.put("/v1/shares/{user_id}", dependencies=auth)
    def put_share(user_id: str, body: StoreIn) -> dict:
        st = get_store()
        payload = st.open_json(body.sealed)  # проверяем формат, храним запечатанным как есть
        if payload.get("v") != 1 or len(base64.b64decode(payload.get("auth", ""))) != 32 or not payload.get("share"):
            raise HTTPException(400, "bad share payload")
        with st.lock:
            st.db.execute(
                "INSERT INTO shares(user_id, version, sealed, attempts, updated_at) VALUES (?,?,?,0,?) "
                "ON CONFLICT(user_id) DO UPDATE SET version=excluded.version, sealed=excluded.sealed, "
                "attempts=0, updated_at=excluded.updated_at WHERE excluded.version >= shares.version",
                (user_id, body.version, body.sealed, time.time()),
            )
            st.db.commit()
        return {"status": "stored"}

    @app.post("/v1/shares/{user_id}/recover", dependencies=auth)
    def recover(user_id: str, body: RecoverIn) -> dict:
        st = get_store()
        request = st.open_json(body.sealed_request)
        with st.lock:
            row = st.db.execute("SELECT sealed, attempts FROM shares WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "no share")
            stored = st.open_json(row[0])
            given = base64.b64decode(request.get("auth", ""))
            expected = base64.b64decode(stored["auth"])
            if hmac.compare_digest(hashlib.sha256(given).digest(), hashlib.sha256(expected).digest()):
                st.db.execute("UPDATE shares SET attempts=0 WHERE user_id=?", (user_id,))
                st.db.commit()
                sealed_share = ecies.seal(request.get("client_pub", ""), json.dumps({"share": stored["share"]}).encode())
                return {"status": "ok", "sealed_share": sealed_share}
            attempts = row[1] + 1
            if attempts >= limit:
                st.db.execute("DELETE FROM shares WHERE user_id=?", (user_id,))
                st.db.commit()
                return {"status": "destroyed", "attempts_left": 0}
            st.db.execute("UPDATE shares SET attempts=? WHERE user_id=?", (attempts, user_id))
            st.db.commit()
            return {"status": "wrong_pin", "attempts_left": limit - attempts}

    @app.delete("/v1/shares/{user_id}", status_code=204, dependencies=auth)
    def destroy(user_id: str) -> None:
        st = get_store()
        with st.lock:
            st.db.execute("DELETE FROM shares WHERE user_id=?", (user_id,))
            st.db.commit()

    return app


app = create_app()
