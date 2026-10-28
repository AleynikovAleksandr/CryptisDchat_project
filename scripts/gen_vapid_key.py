"""Создать ключ VAPID для Web Push и вывести строки для .env.

    python scripts/gen_vapid_key.py

Ключ задаётся один раз: после смены все браузеры должны подписаться заново
(клиент делает это сам при следующем открытии приложения).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.webpush import generate_vapid_private_key, vapid_public_key  # noqa: E402

key = generate_vapid_private_key()
print(f"VAPID_PRIVATE_KEY={key}")
print("VAPID_SUBJECT=mailto:admin@example.com")
print(f"# публичный ключ (клиент получает его сам через /api/push/vapid-public-key): {vapid_public_key(key)}")
