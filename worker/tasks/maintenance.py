"""Очередь `maintenance` — рутинная «бухгалтерия» состояния (низкий приоритет)."""
from __future__ import annotations

import gzip
import logging
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select

from app.config import get_settings
from app.models import Attachment, KeyRecoveryRequest, Message, RefreshToken, ThreadMember, utcnow
from app.services.storage import LocalFileStorage
from worker.celery_app import celery_app
from worker.runtime import cache_redis, db

log = logging.getLogger(__name__)

BACKUP_DIR = Path(os.environ.get("DB_BACKUP_DIR", "/app/data_warehouses/backups"))
BACKUP_KEEP = 7


@celery_app.task(name="maintenance.purge_expired_messages")
def purge_expired_messages() -> int:
    """Исчезающие сообщения: удалить шифротекст и вложения, у которых истёк срок."""
    now = utcnow()
    storage = LocalFileStorage(get_settings().uploads_dir)
    with db() as session:
        expired = list(session.scalars(select(Message).where(Message.expires_at <= now).limit(5000)))
        att_ids = [m.attachment_id for m in expired if m.attachment_id]
        if att_ids:
            for att in session.scalars(select(Attachment).where(Attachment.id.in_(att_ids))):
                storage.delete(att.storage_path)
                att.deleted_at = now
        for m in expired:
            session.delete(m)
        session.commit()
    return len(expired)


@celery_app.task(name="maintenance.reconcile_unread")
def reconcile_unread() -> int:
    """Сверить счётчики unread:{user}:{thread} в Redis с БД, чтобы рассинхрон не жил вечно (3.3)."""
    r = cache_redis()
    fixed = 0
    with db() as session:
        for key in r.scan_iter(match="unread:*", count=500):
            _, user_id, thread_id = key.split(":", 2)
            member = session.get(ThreadMember, (thread_id, user_id))
            if member is None or member.left_at is not None:
                r.delete(key)
                continue
            floor = max(member.last_read_seq, member.cleared_seq)
            real = session.scalar(
                select(func.count()).select_from(Message).where(
                    Message.thread_id == thread_id, Message.seq > floor, Message.sender_id != user_id)
            ) or 0
            if str(real) != r.get(key):
                r.set(key, real)
                fixed += 1
    return fixed


@celery_app.task(name="maintenance.cleanup_tokens")
def cleanup_tokens() -> None:
    now = utcnow()
    with db() as session:
        session.execute(delete(RefreshToken).where(RefreshToken.expires_at < now - timedelta(days=30)))
        for req in session.scalars(select(KeyRecoveryRequest).where(
                KeyRecoveryRequest.created_at < now - timedelta(days=1), KeyRecoveryRequest.result_json.is_not(None))):
            req.result_json = None
        session.commit()


@celery_app.task(name="maintenance.backup_database")
def backup_database() -> str | None:
    """Ежедневный дамп MariaDB (Ports_and_Database.md 3.5) с ротацией последних BACKUP_KEEP файлов."""
    s = get_settings()
    if s.sync_db_url.startswith("sqlite") or shutil.which("mariadb-dump") is None:
        log.warning("database backup skipped: mariadb-dump is not available")
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"cryptisdchat-{utcnow():%Y%m%d-%H%M%S}.sql.gz"
    env = {**os.environ, "MYSQL_PWD": s.db_password}
    cmd = ["mariadb-dump", "-h", s.db_host, "-P", str(s.db_port), "-u", s.db_user,
           "--single-transaction", "--quick", "--routines", s.db_name]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, env=env) as proc, gzip.open(target, "wb") as out:
        assert proc.stdout is not None
        shutil.copyfileobj(proc.stdout, out)
    if proc.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"mariadb-dump exited with {proc.returncode}")
    for old in sorted(BACKUP_DIR.glob("cryptisdchat-*.sql.gz"))[:-BACKUP_KEEP]:
        old.unlink()
    return str(target)
