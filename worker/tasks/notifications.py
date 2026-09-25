"""Очередь `notifications` — push через APNs/FCM (Gunicorn_Celery.md 4.6).

Текст сообщения сервер не знает: в push уходит безопасный фрагмент от клиента или «New message».
"""
from __future__ import annotations

from sqlalchemy import delete, select

from app.interfaces.push import PushMessage
from app.models import PushToken
from worker.celery_app import celery_app
from worker.runtime import db, push_sender


@celery_app.task(name="notifications.send_push", ignore_result=True)
def send_push(user_id: str, title: str, body: str, data: dict) -> int:
    sender = push_sender()
    sent = 0
    with db() as session:
        tokens = list(session.scalars(select(PushToken).where(PushToken.user_id == user_id)))
        invalid: list[str] = []
        for t in tokens:
            ok = sender.send(PushMessage(token=t.token, platform=t.platform, title=title[:80], body=body[:120],
                                         data={k: str(v) for k, v in data.items()}))
            if ok:
                sent += 1
            else:
                invalid.append(t.id)
        if invalid:
            session.execute(delete(PushToken).where(PushToken.id.in_(invalid)))
            session.commit()
    return sent
