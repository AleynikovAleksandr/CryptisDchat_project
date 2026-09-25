"""Конфигурация приложения.

Все параметры читаются из переменных окружения (файл .env в docker-compose).
Один и тот же модуль используется FastAPI, Flask-админкой и Celery-воркером,
чтобы у всех процессов был единый источник настроек.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- общее ---
    app_env: str = Field("production", description="production | development | test")
    app_name: str = "CryptisDchat"
    public_origin: str = "http://localhost:3890"
    allowed_ws_origins: str = "http://localhost:3890"

    # --- база данных (MariaDB) ---
    db_host: str = "db"
    db_port: int = 3306
    db_name: str = "cryptisdchat"
    db_user: str = "cryptis_user"
    db_password: str = ""
    database_url: str | None = None          # явный URL перекрывает поля выше (тесты: sqlite+aiosqlite)
    sync_database_url: str | None = None     # для Flask и Celery (pymysql)
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- Redis: логическое разделение по номерам DB (Gunicorn_Celery.md, 3.4) ---
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db_cache: int = 0
    redis_db_broker: int = 1
    redis_db_results: int = 2
    redis_db_pubsub: int = 3

    # --- JWT / сессии (ТЗ 4.2) ---
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_days: int = 60
    # устройство без входов дольше этого срока завершается (например, после очистки данных сайта)
    device_inactive_days: int = 30
    # срок cookie устройства (предел браузеров — 400 дней); отозванные устройства старше удаляются
    device_cookie_max_age_days: int = 400

    # --- ton_proof (ТЗ 4.2) ---
    ton_proof_domain: str = "localhost:3890"
    ton_proof_ttl_seconds: int = 15 * 60
    ton_proof_secret: str = "change-me-too"
    ton_network: str = "-239"                 # -239 mainnet, -3 testnet
    dev_wallet_login: bool = False            # только для локальной разработки

    # --- блокчейн (Gunicorn_Celery.md, 4.2–4.4) ---
    blockchain_mode: str = "mock"             # mock | toncenter
    toncenter_endpoint: str = "https://toncenter.com/api/v2"
    toncenter_api_key: str = ""
    service_wallet_mnemonic: str = ""
    service_wallet_version: str = "v4r2"
    chain_batch_max_items: int = 500
    chain_batch_interval_seconds: int = 120
    chain_required_confirmations: int = 1

    # --- резервное копирование ключей (ТЗ 6.6) ---
    realm_urls: str = "http://realm1:9000,http://realm2:9000,http://realm3:9000"
    realm_api_token: str = "realm-internal-token"
    backup_threshold: int = 2
    backup_max_pin_attempts: int = 10

    # --- файлы ---
    uploads_dir: str = "/app/uploads"
    max_upload_bytes: int = 25 * 1024 * 1024

    # --- rate limiting (ТЗ 7) ---
    rate_limit_default: str = "120/60"        # запросов / секунд
    rate_limit_auth: str = "20/60"

    # --- Flask-админка ---
    admin_secret_key: str = "change-me-admin"
    admin_username: str = "admin"
    admin_password: str = ""
    admin_allowed_ips: str = ""               # пусто = без ограничения по IP
    webhook_secret: str = "change-me-webhook"

    # ------------------------------------------------------------------
    def check_production_secrets(self) -> None:
        """В production запрещаем запуск с секретами по умолчанию и dev-входом."""
        if self.is_dev:
            return
        weak = [name for name in ("jwt_secret", "ton_proof_secret", "admin_secret_key", "webhook_secret")
                if getattr(self, name).startswith("change-me") or len(getattr(self, name)) < 32]
        if weak:
            raise RuntimeError(f"set strong values (32+ chars) for: {', '.join(n.upper() for n in weak)}")
        if self.dev_wallet_login:
            raise RuntimeError("DEV_WALLET_LOGIN must be false in production")

    @property
    def is_dev(self) -> bool:
        return self.app_env in {"development", "test"}

    @property
    def async_db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"mysql+asyncmy://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/"
            f"{self.db_name}?charset=utf8mb4"
        )

    @property
    def sync_db_url(self) -> str:
        if self.sync_database_url:
            return self.sync_database_url
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/"
            f"{self.db_name}?charset=utf8mb4"
        )

    def redis_url(self, db: int) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{db}"

    @property
    def realm_url_list(self) -> list[str]:
        return [u.strip() for u in self.realm_urls.split(",") if u.strip()]

    @property
    def ws_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_ws_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
