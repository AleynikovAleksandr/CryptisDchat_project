#!/usr/bin/env python3
"""Локальный запуск всего CryptisDchat без Docker — для разработки и ручной проверки.

    .venv/bin/python scripts/dev_server.py

Поднимает в одном процессе (и двух дочерних для Celery):
  * Redis 6 (pip-пакет redislite; иначе fakeredis)   127.0.0.1:6399
  * SQLite вместо MariaDB                             data_warehouses/dev/dev.db
  * три realm-сервиса резервного копирования ключей   127.0.0.1:9101..9103
  * Celery worker (все очереди) и Celery beat         дочерние процессы
  * Flask-админка (admin / devpassword123)            http://127.0.0.1:3891/admin/
  * FastAPI: API + WebSocket + страницы               http://localhost:3890

Блокчейн — mock (подтверждение через ~3 с), вход — dev-кошелёк (DEV_WALLET_LOGIN).
Боевой запуск — docker compose up (см. README.md).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data_warehouses" / "dev"
DATA.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": f"sqlite+aiosqlite:///{DATA}/dev.db",
    "SYNC_DATABASE_URL": f"sqlite:///{DATA}/dev.db",
    "REDIS_HOST": "127.0.0.1",
    "REDIS_PORT": "6399",
    "JWT_SECRET": "dev-jwt-secret-please-change-0123456789abcdef",
    "TON_PROOF_SECRET": "dev-ton-proof-secret",
    "TON_PROOF_DOMAIN": "localhost:3890",
    "PUBLIC_ORIGIN": "http://localhost:3890",
    "ALLOWED_WS_ORIGINS": "http://localhost:3890,http://127.0.0.1:3890",
    "DEV_WALLET_LOGIN": "true",
    "UPLOADS_DIR": str(DATA / "uploads"),
    "BLOCKCHAIN_MODE": "mock",
    "CHAIN_BATCH_INTERVAL_SECONDS": "15",
    "REALM_URLS": "http://127.0.0.1:9101,http://127.0.0.1:9102,http://127.0.0.1:9103",
    "REALM_API_TOKEN": "dev-realm-token",
    "ADMIN_SECRET_KEY": "dev-admin-secret",
    "WEBHOOK_SECRET": "dev-webhook-secret",
    "DB_BACKUP_DIR": str(DATA / "backups"),
}
os.environ.update(ENV)


def start_redis() -> subprocess.Popen | None:
    """Настоящий redis-server из pip-пакета redislite; без него — fakeredis (Celery тогда может не работать)."""
    try:
        import redislite

        binary = Path(redislite.__file__).parent / "bin" / "redis-server"
        return subprocess.Popen([str(binary), "--port", "6399", "--bind", "127.0.0.1", "--save", "",
                                 "--appendonly", "no"], stdout=subprocess.DEVNULL)
    except ImportError:
        from fakeredis import TcpFakeServer

        print("  ! redislite is not installed — using fakeredis (pip install redislite for Celery)")
        server = TcpFakeServer(("127.0.0.1", 6399), server_type="redis")
        threading.Thread(target=server.serve_forever, daemon=True, name="redis").start()
        return None


def init_db() -> None:
    from sqlalchemy import create_engine

    from app.models import Base

    engine = create_engine(os.environ["SYNC_DATABASE_URL"])
    Base.metadata.create_all(engine)
    engine.dispose()


def serve(app, port: int, name: str) -> None:
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    threading.Thread(target=uvicorn.Server(config).run, daemon=True, name=name).start()


def start_realms() -> None:
    from realm.app import create_app as realm_app

    for i in range(3):
        serve(realm_app(data_dir=DATA / f"realm{i + 1}", api_token=ENV["REALM_API_TOKEN"]), 9101 + i, f"realm{i + 1}")


def start_admin() -> None:
    from werkzeug.serving import make_server

    from admin_app import create_app, db
    from admin_app.commands import upsert_admin

    app = create_app()
    with app.app_context():
        from sqlalchemy import select

        from app.models import AdminUser
        if db.session.scalar(select(AdminUser).where(AdminUser.username == "admin")) is None:
            upsert_admin("admin", "devpassword123")
    server = make_server("127.0.0.1", 3891, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True, name="admin").start()


def start_celery() -> list[subprocess.Popen]:
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'backend'}{os.pathsep}{ROOT}"}
    celery = [sys.executable, "-m", "celery", "-A", "worker.celery_app"]
    procs = [
        subprocess.Popen(celery + ["worker", "-Q", "blockchain,keys,notifications,media,maintenance",
                                   "--concurrency=2", "--loglevel=warning", "--pool=threads"], env=env, cwd=ROOT),
        subprocess.Popen(celery + ["beat", "--loglevel=warning",
                                   "--schedule", str(DATA / "celerybeat-schedule")], env=env, cwd=ROOT),
    ]
    return procs


def main() -> None:
    redis_proc = start_redis()
    time.sleep(0.5)
    init_db()
    start_realms()
    start_admin()
    procs = start_celery() + ([redis_proc] if redis_proc else [])
    time.sleep(1.5)
    # публичные ключи realm нужны клиентам для резервной копии — публикуем сразу
    from worker.tasks.keys import refresh_realm_keys
    refresh_realm_keys()

    def shutdown(*_):
        for p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("\n  CryptisDchat dev stack is up")
    print("  ─ app:    http://localhost:3890        (dev wallet login; add ?dev=new for another user)")
    print("  ─ admin:  http://127.0.0.1:3891/admin/  (admin / devpassword123)\n")

    import uvicorn

    from app.main import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=3890, log_level="info")
    shutdown()


if __name__ == "__main__":
    main()
