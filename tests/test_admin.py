"""Flask-админка: вход с CSRF и блокировкой перебора, журнал действий, отзыв сессий,
ручной повтор батча, HMAC-вебхуки, метрики."""
from __future__ import annotations

import hashlib
import hmac
import json
import re

import pytest
from sqlalchemy import select

from admin_app import create_app, db
from admin_app.commands import upsert_admin
from app.config import get_settings
from app.models import AdminAuditLog, ChainBatch, Device, User, utcnow


@pytest.fixture
def admin_client(infra):
    sent: list[tuple] = []
    app = create_app(get_settings(), TESTING=True, TASK_SENDER=lambda name, *a: sent.append((name, a)),
                     REDIS_CLIENT=infra.sync_redis)
    with app.app_context():
        upsert_admin("root", "correct-horse-battery")
    client = app.test_client()
    client.sent = sent
    client.flask_app = app
    return client


def csrf(client, url: str) -> str:
    html = client.get(url).get_data(as_text=True)
    return re.search(r'name="csrf_token" type="hidden" value="([^"]+)"', html).group(1)


def sign_in(client, password="correct-horse-battery"):
    token = csrf(client, "/admin/login")
    return client.post("/admin/login", data={"csrf_token": token, "username": "root", "password": password})


def test_login_requires_csrf_and_valid_password(admin_client):
    r = admin_client.post("/admin/login", data={"username": "root", "password": "correct-horse-battery"})
    assert r.status_code == 400  # нет CSRF-токена
    assert "Invalid" in sign_in(admin_client, "wrong").get_data(as_text=True)
    r = sign_in(admin_client)
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/")
    assert admin_client.get("/admin/").status_code == 200


def test_lockout_after_failed_attempts(admin_client):
    for _ in range(5):
        sign_in(admin_client, "nope")
    r = sign_in(admin_client)
    assert r.status_code == 429


def test_pages_require_login(admin_client):
    for path in ("/admin/", "/admin/users", "/admin/reports", "/admin/blockchain-queue", "/admin/metrics"):
        assert admin_client.get(path).status_code == 302


async def test_revoke_sessions_and_suspend_are_audited(admin_client, make_user):
    u = await make_user("Mallory")
    sign_in(admin_client)
    assert "Mallory" in admin_client.get("/admin/users?q=Mallory").get_data(as_text=True)
    token = csrf(admin_client, f"/admin/users/{u.id}")
    r = admin_client.post(f"/admin/users/{u.id}/revoke-sessions", data={"csrf_token": token})
    assert r.status_code == 302
    assert (await u.get("/api/me")).status_code == 401

    token = csrf(admin_client, f"/admin/users/{u.id}")
    admin_client.post(f"/admin/users/{u.id}/suspend", data={"csrf_token": token, "reason": "spam"})
    with admin_client.flask_app.app_context():
        user = db.session.get(User, u.id)
        assert user.is_suspended and user.suspended_reason == "spam"
        assert all(d.revoked_at for d in db.session.scalars(select(Device).where(Device.user_id == u.id)))
        actions = {a.action for a in db.session.scalars(select(AdminAuditLog))}
    assert {"login.success", "user.revoke_sessions", "user.suspend", "user.view"} <= actions


def test_failed_batch_manual_retry(admin_client):
    with admin_client.flask_app.app_context():
        batch = ChainBatch(merkle_root="ab" * 32, leaf_count=3, status="failed", attempts=5, last_error="no balance")
        db.session.add(batch)
        db.session.commit()
        batch_id = batch.id
    sign_in(admin_client)
    page = admin_client.get("/admin/blockchain-queue").get_data(as_text=True)
    assert "no balance" in page
    token = csrf(admin_client, "/admin/blockchain-queue")
    admin_client.post(f"/admin/blockchain-queue/retry/{batch_id}", data={"csrf_token": token})
    assert admin_client.sent == [("blockchain.retry_batch", (batch_id,))]


def test_webhook_requires_hmac(admin_client):
    with admin_client.flask_app.app_context():
        db.session.add(ChainBatch(merkle_root="cd" * 32, leaf_count=1, status="submitted", tx_hash="ref:abc",
                                  submitted_at=utcnow()))
        db.session.commit()
    body = json.dumps({"tx_ref": "ref:abc"}).encode()
    assert admin_client.post("/webhooks/ton-tx-status", data=body).status_code == 401
    sig = hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    r = admin_client.post("/webhooks/ton-tx-status", data=body, headers={"X-Cryptis-Signature": sig},
                          content_type="application/json")
    assert r.status_code == 200 and admin_client.sent[0][0] == "blockchain.confirm_batch"


def test_metrics_and_healthz(admin_client):
    assert admin_client.get("/healthz").json == {"status": "ok"}
    sign_in(admin_client)
    text = admin_client.get("/admin/metrics").get_data(as_text=True)
    assert "cryptis_users_total" in text and 'cryptis_chain_batches{status="failed"}' in text
