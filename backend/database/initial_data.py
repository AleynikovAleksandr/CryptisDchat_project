"""Инициализация данных после первого запуска.

Схема таблиц создаётся MariaDB из database/schema.sql при инициализации тома.
Этот скрипт создаёт (или обновляет пароль) администратора Flask-панели из ADMIN_USERNAME /
ADMIN_PASSWORD и проверяет, что схема на месте:

    docker compose run --rm admin python database/initial_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect  # noqa: E402

from admin_app import create_app, db  # noqa: E402
from admin_app.commands import upsert_admin  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import Base  # noqa: E402


def main() -> int:
    settings = get_settings()
    app = create_app(settings)
    with app.app_context():
        existing = set(inspect(db.engine).get_table_names())
        missing = sorted(set(Base.metadata.tables) - existing)
        if missing:
            print(f"schema is incomplete, missing tables: {', '.join(missing)}", file=sys.stderr)
            print("apply backend/database/schema.sql first", file=sys.stderr)
            return 1
        if not settings.admin_password:
            print("ADMIN_PASSWORD is empty — admin user not created", file=sys.stderr)
            return 1
        try:
            upsert_admin(settings.admin_username, settings.admin_password)
        except ValueError as exc:
            print(f"ADMIN_PASSWORD rejected: {exc}", file=sys.stderr)
            return 1
        print(f"admin '{settings.admin_username}' is ready; {len(existing)} tables present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
