"""Служебные вебхуки и проверка живости (Interface_and_API.md, раздел 9).

Вебхуки аутентифицируются HMAC-SHA256 подписью тела (заголовок X-Cryptis-Signature)
с общим секретом WEBHOOK_SECRET — CSRF для них неприменим.
"""
from __future__ import annotations

import hashlib
import hmac
import json

from flask import Blueprint, abort, jsonify, request
from sqlalchemy import select

from admin_app import db
from admin_app.views import audit, enqueue, settings
from app.models import ChainBatch, GroupMembershipEvent

bp = Blueprint("webhooks", __name__)


def _verify_signature() -> dict:
    body = request.get_data()
    expected = hmac.new(settings().webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    given = request.headers.get("X-Cryptis-Signature", "")
    if not hmac.compare_digest(expected, given):
        abort(401)
    try:
        return json.loads(body or b"{}")
    except ValueError:
        abort(400)


@bp.post("/webhooks/ton-tx-status")
def ton_tx_status():
    """Колбэк/результат опроса ноды TON о статусе транзакции: ставит короткую задачу подтверждения
    (Gunicorn_Celery.md 4.3). Сам статус не принимается на веру — задача перепроверит его в сети."""
    data = _verify_signature()
    tx_ref = str(data.get("tx_ref") or data.get("comment") or "")
    if not tx_ref:
        abort(400)
    batch = db.session.scalar(select(ChainBatch).where(ChainBatch.tx_hash == tx_ref))
    if batch is not None:
        enqueue("blockchain.confirm_batch", batch.id)
        return jsonify({"status": "queued", "batch_id": batch.id})
    event = db.session.scalar(select(GroupMembershipEvent).where(GroupMembershipEvent.tx_hash == tx_ref))
    if event is not None:
        enqueue("blockchain.confirm_membership", event.id)
        return jsonify({"status": "queued", "membership_event_id": event.id})
    return jsonify({"status": "unknown"}), 404


@bp.post("/webhooks/tonconnect")
def tonconnect():
    """Служебные колбэки моста TonConnect (если используется мост с колбэками): только журналирование."""
    data = _verify_signature()
    audit("webhook.tonconnect", "tonconnect", str(data.get("event", ""))[:64])
    db.session.commit()
    return jsonify({"status": "ok"})


@bp.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})
