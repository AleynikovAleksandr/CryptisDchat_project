"""Очередь `media` — жизненный цикл зашифрованных вложений (Gunicorn_Celery.md 4.7).

Сервер не генерирует превью (он не видит содержимого): миниатюру делает клиент до
шифрования. Здесь — только хранение и зачистка непрозрачных блобов.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.models import Attachment, UserSettings, utcnow
from app.services.storage import LocalFileStorage
from worker.celery_app import celery_app
from worker.runtime import db

RETENTION_DAYS = {"30d": 30, "60d": 60, "6m": 182}


def _storage() -> LocalFileStorage:
    return LocalFileStorage(get_settings().uploads_dir)


def _delete(attachments: list[Attachment]) -> int:
    storage = _storage()
    now = utcnow()
    for att in attachments:
        storage.delete(att.storage_path)
        att.deleted_at = now
    return len(attachments)


@celery_app.task(name="media.purge_by_retention")
def purge_by_retention() -> int:
    """«Auto-delete media older than»: Keep all / 30 days / 60 days / 6 months."""
    total = 0
    now = utcnow()
    with db() as session:
        for code, days in RETENTION_DAYS.items():
            rows = list(session.scalars(
                select(Attachment).join(UserSettings, UserSettings.user_id == Attachment.owner_id).where(
                    UserSettings.media_retention == code,
                    Attachment.kind.in_(("file", "thumbnail")),
                    Attachment.deleted_at.is_(None),
                    Attachment.created_at < now - timedelta(days=days),
                ).limit(5000)
            ))
            total += _delete(rows)
        session.commit()
    return total


@celery_app.task(name="media.delete_all_for_user")
def delete_all_for_user(user_id: str) -> int:
    """«Delete all media» (/api/settings/delete-media)."""
    with db() as session:
        rows = list(session.scalars(
            select(Attachment).where(Attachment.owner_id == user_id, Attachment.kind.in_(("file", "thumbnail")),
                                     Attachment.deleted_at.is_(None))
        ))
        count = _delete(rows)
        session.commit()
    return count
