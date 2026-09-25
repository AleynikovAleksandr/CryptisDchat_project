"""Задачи Celery: надёжность записи в блокчейн, жизненный цикл медиа, исчезающие сообщения, настройки."""
from __future__ import annotations

import os
from datetime import timedelta

import pytest
from celery.exceptions import Retry
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from app.models import Attachment, ChainBatch, Device, Message, utcnow
from tests import client_crypto as cc
from tests.conftest import login
from tests.test_api_messaging import open_dm
from worker.tasks import blockchain as tb


def sync_session() -> Session:
    return Session(create_engine(os.environ["SYNC_DATABASE_URL"]))


async def test_batch_retries_then_fails_and_manual_retry_recovers(make_user, chain, infra):
    alice, bob = await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    await alice.post(f"/api/threads/{thread_id}/messages", cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "x"}))

    chain.fail_next = 100  # нода недоступна
    with pytest.raises(Retry):
        tb.flush_batch()
    with sync_session() as s:
        batch = s.scalar(select(ChainBatch))
        assert batch.status == "pending" and batch.attempts == 1 and "node unavailable" in batch.last_error
        batch_id = batch.id
    for _ in range(tb.MAX_RETRIES):
        try:
            tb.submit_batch.apply(args=[batch_id], retries=tb.MAX_RETRIES, throw=True)
        except Retry:
            pass
    with sync_session() as s:
        assert s.get(ChainBatch, batch_id).status == "failed"  # видно в /admin/blockchain-queue

    chain.fail_next = 0
    tb.retry_batch(batch_id)
    with sync_session() as s:
        assert s.get(ChainBatch, batch_id).status == "confirmed"
    assert len(chain.sent) == 1


async def test_submit_is_idempotent(make_user, chain, infra):
    alice, bob = await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    await alice.post(f"/api/threads/{thread_id}/messages", cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "x"}))
    batch_id = tb.flush_batch()
    tb.submit_batch(batch_id)  # повтор после «сбоя» — вторая транзакция не отправляется
    assert len(chain.sent) == 1


async def test_orphaned_events_are_swept_back(make_user, chain, infra):
    alice, bob = await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    await alice.post(f"/api/threads/{thread_id}/messages", cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "x"}))
    infra.sync_redis.delete("chain:pending")  # процесс упал между БД и Redis
    from app.models import ChainEvent
    with sync_session() as s:
        s.execute(update(ChainEvent).values(created_at=utcnow() - timedelta(hours=1)))
        s.commit()
    assert tb.sweep_orphans() == 1
    assert tb.flush_batch() is not None


async def test_media_retention_and_delete_all(make_user, infra):
    alice = await make_user()
    ids = []
    for _ in range(2):
        r = await alice.client.post("/api/uploads", headers=alice.h, files={"file": ("f", b"x" * 100)})
        ids.append(r.json()["id"])
    with sync_session() as s:
        s.execute(update(Attachment).where(Attachment.id == ids[0]).values(created_at=utcnow() - timedelta(days=45)))
        s.commit()
    assert (await alice.get("/api/settings")).json()["cached_media_bytes"] == 200
    infra.tasks.enqueue("media.purge_by_retention")          # 30 дней по умолчанию
    assert (await alice.get("/api/settings")).json()["cached_media_bytes"] == 100
    assert (await alice.post("/api/settings/delete-media")).status_code == 202
    assert (await alice.get("/api/settings")).json()["cached_media_bytes"] == 0


async def test_expired_messages_are_purged(make_user, infra):
    alice, bob = await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    await alice.post(f"/api/threads/{thread_id}/messages", cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "x"}))
    with sync_session() as s:
        s.execute(update(Message).values(expires_at=utcnow() - timedelta(seconds=1)))
        s.commit()
    infra.tasks.enqueue("maintenance.purge_expired_messages")
    assert (await bob.get(f"/api/threads/{thread_id}/messages")).json()["items"] == []



async def test_inactive_devices_are_ended_and_old_revoked_ones_deleted(client, infra):
    wallet = cc.DevWallet()
    stale, fresh = await login(client, wallet), await login(client, wallet)
    hf = {"Authorization": f"Bearer {fresh['access_token']}"}
    devices = {d["current"]: d["device_id"] for d in (await client.get("/api/auth/sessions", headers=hf)).json()}
    stale_id, fresh_id = devices[False], devices[True]
    with sync_session() as s:
        s.execute(update(Device).where(Device.id == stale_id).values(last_seen_at=utcnow() - timedelta(days=31)))
        s.commit()

    infra.tasks.enqueue("maintenance.cleanup_devices")
    sessions = (await client.get("/api/auth/sessions", headers=hf)).json()
    assert [d["device_id"] for d in sessions] == [fresh_id]
    r = await client.post("/api/auth/refresh", json={"refresh_token": stale["refresh_token"]})
    assert r.status_code == 401

    # вернулись в тот же браузер — то же устройство, а не новое
    back = await login(client, wallet, device_cookie=stale["_device"])
    sessions = (await client.get("/api/auth/sessions", headers={"Authorization": f"Bearer {back['access_token']}"})).json()
    assert {d["device_id"] for d in sessions} == {stale_id, fresh_id}

    # отозванное дольше срока cookie устройство вернуть нечем — удаляется
    with sync_session() as s:
        s.execute(update(Device).where(Device.id == stale_id).values(revoked_at=utcnow() - timedelta(days=401)))
        s.commit()
    infra.tasks.enqueue("maintenance.cleanup_devices")
    with sync_session() as s:
        assert s.get(Device, stale_id) is None and s.get(Device, fresh_id) is not None

async def test_settings_roundtrip_and_validation(make_user):
    u = await make_user()
    s = (await u.get("/api/settings")).json()
    assert s["autolock"] == "1m" and s["group_invite_policy"] == "everyone"
    r = await u.patch("/api/settings", {"autolock": "30m", "markdown_preview": True, "media_retention": "keep"})
    assert r.json()["autolock"] == "30m" and r.json()["markdown_preview"] is True
    assert (await u.patch("/api/settings", {"autolock": "forever"})).status_code == 422


async def test_unread_counter_reconciliation(make_user, infra):
    alice, bob = await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    await alice.post(f"/api/threads/{thread_id}/messages", cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "x"}))
    infra.sync_redis.set(f"unread:{bob.id}:{thread_id}", 42)  # рассинхрон после сбоя
    infra.tasks.enqueue("maintenance.reconcile_unread")
    assert (await bob.get("/api/threads")).json()[0]["unread"] == 1


async def test_contacts_search_and_reports(make_user):
    alice, bob = await make_user("Alice Renner"), await make_user("Bob")
    await bob.patch("/api/me", {"username": "bobby"})
    found = (await alice.get("/api/contacts?query=bob")).json()
    assert [u["id"] for u in found] == [bob.id]
    by_addr = (await alice.get(f"/api/contacts?query={bob.wallet.address[2:14]}")).json()
    assert [u["id"] for u in by_addr] == [bob.id]
    r = await alice.post("/api/reports", {"reason": "spam", "target_user_id": bob.id, "details": "ads"})
    assert r.status_code == 201
