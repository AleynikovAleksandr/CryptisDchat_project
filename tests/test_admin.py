"""Flask-админка: вход с CSRF и блокировкой перебора, журнал действий, отзыв сессий,
ручной повтор батча, HMAC-вебхуки, метрики."""
from __future__ import annotations

import hashlib
import hmac
import json
import re

import pytest
from sqlalchemy import event, func, select, text
from werkzeug.security import generate_password_hash

from admin_app import create_app, db
from admin_app.commands import deactivate_other_admins, upsert_admin
from app.config import get_settings
from app.models import (
    AdminAuditLog,
    Attachment,
    BlockedUser,
    ChainBatch,
    ConversationKey,
    Device,
    GroupMembershipEvent,
    Message,
    Thread,
    ThreadMember,
    User,
    UserWallet,
    utcnow,
)
from tests import client_crypto as cc
from tests.test_api_messaging import open_dm


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


def test_admin_from_password_hash_replaces_old_login(admin_client):
    """ADMIN_PASSWORD_HASH: пароль не хранится открытым текстом; старый логин после смены не работает."""
    with admin_client.flask_app.app_context():
        with pytest.raises(ValueError):
            upsert_admin("ops", password_hash="1234567")  # не хеш — отказ
        upsert_admin("ops", password_hash=generate_password_hash("1234567"))
        assert deactivate_other_admins("ops") == 1
    assert "Invalid" in sign_in(admin_client).get_data(as_text=True)  # root отключён
    token = csrf(admin_client, "/admin/login")
    r = admin_client.post("/admin/login", data={"csrf_token": token, "username": "ops", "password": "1234567"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/")


def test_lockout_after_failed_attempts(admin_client):
    for _ in range(5):
        sign_in(admin_client, "nope")
    r = sign_in(admin_client)
    assert r.status_code == 429


def test_root_redirects_to_panel(admin_client):
    r = admin_client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/")


def test_pages_require_login(admin_client):
    for path in ("/admin/", "/admin/users", "/admin/users/x/delete", "/admin/reports", "/admin/blockchain-queue",
                 "/admin/metrics"):
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


async def test_delete_user_removes_everything_and_keeps_other_groups(admin_client, make_user):
    mallory, alice, bob = await make_user("Mallory"), await make_user("Alice"), await make_user("Bob")
    dm, key = await open_dm(mallory, alice)
    blob = (await mallory.client.post("/api/uploads", headers=mallory.h, files={"file": ("x.bin", b"x" * 40)},
                                      data={"kind": "file"})).json()["id"]
    p = cc.make_packet(mallory.keys, dm, key, 1, {"type": "file", "name": "a.pdf"}, kind="file", attachment_id=blob)
    assert (await mallory.post(f"/api/threads/{dm}/messages", p)).status_code == 201
    assert (await alice.post(f"/api/threads/{dm}/messages",
                             cc.make_packet(alice.keys, dm, key, 1, {"text": "hi"}))).status_code == 201
    own_group = (await mallory.post("/api/groups", {"title": "Mine", "member_ids": [alice.id]})).json()["id"]
    other_group = (await alice.post("/api/groups", {"title": "Crew", "member_ids": [mallory.id, bob.id]})).json()["id"]
    kept_dm, _ = await open_dm(alice, bob)
    assert (await alice.post(f"/api/blocklist/{mallory.id}")).status_code == 204

    app = admin_client.flask_app
    with app.app_context():
        # в SQLite внешние ключи выключены по умолчанию — включаем, чтобы порядок удаления проверялся как в MariaDB
        event.listen(db.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        db.engine.dispose()
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        path = db.session.get(Attachment, blob).storage_path
        for sender, seq in ((mallory.id, 1), (alice.id, 2)):  # сообщения в чужой группе
            db.session.add(Message(thread_id=other_group, seq=seq, sender_id=sender, client_msg_id=f"c{seq}",
                                   ciphertext=b"x", nonce=b"n" * 24, signature=b"s", key_epoch=1,
                                   sender_key_version=1, content_hash="h"))
        db.session.commit()

    sign_in(admin_client)
    listing = admin_client.get("/admin/users").get_data(as_text=True)
    assert all(name in listing for name in ("Mallory", "Alice", "Bob")) and "3 users" in listing
    page = admin_client.get(f"/admin/users/{mallory.id}/delete").get_data(as_text=True)
    assert "Mallory" in page and "Delete permanently" in page
    token = csrf(admin_client, f"/admin/users/{mallory.id}/delete")

    # без подтверждающего слова ничего не удаляется
    admin_client.post(f"/admin/users/{mallory.id}/delete", data={"csrf_token": token, "confirm": "yes"})
    with app.app_context():
        assert db.session.get(User, mallory.id) is not None

    r = admin_client.post(f"/admin/users/{mallory.id}/delete", data={"csrf_token": token, "confirm": "DELETE"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/users")
    assert (await mallory.get("/api/me")).status_code == 401

    with app.app_context():
        s = db.session
        assert s.get(User, mallory.id) is None
        for model, col in ((UserWallet, UserWallet.user_id), (Device, Device.user_id),
                           (ThreadMember, ThreadMember.user_id), (Message, Message.sender_id),
                           (Attachment, Attachment.owner_id), (BlockedUser, BlockedUser.blocked_user_id),
                           (ConversationKey, ConversationKey.recipient_id)):
            assert s.scalar(select(func.count()).select_from(model).where(col == mallory.id)) == 0, model
        # личный чат и группа, созданная пользователем, удалены вместе с перепиской
        assert s.get(Thread, dm) is None and s.get(Thread, own_group) is None
        assert s.scalar(select(func.count()).select_from(Message).where(Message.thread_id == dm)) == 0
        # чужая группа осталась: участник исключён, сообщения других на месте, ключи будут повёрнуты
        assert s.get(Thread, other_group) is not None and s.get(Thread, kept_dm) is not None
        assert {m.user_id for m in s.scalars(select(ThreadMember).where(ThreadMember.thread_id == other_group))} \
            == {alice.id, bob.id}
        assert [m.sender_id for m in s.scalars(select(Message).where(Message.thread_id == other_group))] == [alice.id]
        removal = s.scalar(select(GroupMembershipEvent).where(GroupMembershipEvent.thread_id == other_group,
                                                              GroupMembershipEvent.action == "remove"))
        assert removal.subject_user_id == mallory.id and removal.actor_id == alice.id
        audit_row = s.scalar(select(AdminAuditLog).where(AdminAuditLog.action == "user.delete"))
        assert audit_row.target_id == mallory.id and "Mallory" in audit_row.details

    assert ("keys.destroy_all", (mallory.id,)) in admin_client.sent
    assert ("media.delete_files", ([path],)) in admin_client.sent
    assert ("blockchain.anchor_membership", (removal.id,)) in admin_client.sent
    assert {t["id"] for t in (await alice.get("/api/threads")).json()} == {other_group, kept_dm}
