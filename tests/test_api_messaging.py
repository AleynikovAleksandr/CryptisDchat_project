"""Личные чаты: Conversation key через ECIES, подписанные шифротексты, история, поиск,
квитанции, блокчейн-доказательства, вложения, блокировки, исчезающие сообщения."""
from __future__ import annotations

import base64
import os

from app.proto import wire
from app.services import merkle
from realm import ecies
from tests import client_crypto as cc


async def open_dm(a, b) -> tuple[str, bytes]:
    """a открывает чат с b и публикует Conversation key, обёрнутый для обоих (ТЗ 6.3)."""
    r = await a.post("/api/threads", {"user_id": b.id})
    assert r.status_code == 200, r.text
    thread_id = r.json()["id"]
    conv_key = os.urandom(32)
    keys_b = (await a.get(f"/api/keys/{b.id}")).json()
    wrapped = [
        {"recipient_id": a.id, "recipient_key_version": 1,
         "wrapped_key": ecies.seal(a.keys.public()["identity_pub"], conv_key)},
        {"recipient_id": b.id, "recipient_key_version": keys_b["version"],
         "wrapped_key": ecies.seal(keys_b["identity_pub"], conv_key)},
    ]
    r = await a.post(f"/api/threads/{thread_id}/keys", {"epoch": 1, "keys": wrapped})
    assert r.status_code == 201, r.text
    return thread_id, conv_key


async def test_dm_end_to_end(make_user, chain, infra):
    alice, bob = await make_user("Alice"), await make_user("Bob")
    thread_id, _ = await open_dm(alice, bob)

    # Bob получает Conversation key и разворачивает его своим приватным Identity-ключом
    [ck] = (await bob.get(f"/api/threads/{thread_id}/keys")).json()
    conv_key = ecies.open_sealed(bob.keys.identity, ck["wrapped_key"])

    packet = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "**Hello** Bob, meeting tomorrow"})
    r = await alice.post(f"/api/threads/{thread_id}/messages", packet)
    assert r.status_code == 201, r.text
    msg = r.json()
    assert msg["seq"] == 1

    # повторная отправка того же client_msg_id идемпотентна
    again = await alice.post(f"/api/threads/{thread_id}/messages", packet)
    assert again.json()["id"] == msg["id"]

    history = (await bob.get(f"/api/threads/{thread_id}/messages")).json()["items"]
    assert [m["id"] for m in history] == [msg["id"]]
    assert cc.decrypt_message(conv_key, history[0])["text"].startswith("**Hello**")

    # сервер хранит только шифротекст
    from sqlalchemy import create_engine, text
    engine = create_engine(os.environ["SYNC_DATABASE_URL"])
    with engine.connect() as conn:
        raw = conn.execute(text("SELECT ciphertext FROM messages")).scalar_one()
    engine.dispose()
    assert b"Hello" not in raw

    # список чатов (боковая панель): непрочитанное у Bob
    threads = (await bob.get("/api/threads")).json()
    assert threads[0]["unread"] == 1 and threads[0]["peer"]["display_name"] == "Alice"
    assert (await bob.get("/api/threads?filter=groups")).json() == []

    # прочтение → квитанция + событие в блокчейн-журнал
    assert (await bob.post(f"/api/threads/{thread_id}/read", {})).json()["last_read_seq"] == 1
    assert (await bob.get("/api/threads")).json()[0]["unread"] == 0
    assert (await alice.get("/api/threads")).json()[0]["peer_last_read_seq"] >= 1

    # батч в TON: API → очередь Redis → Celery flush → Меркл → транзакция служебного кошелька
    infra.tasks.enqueue("blockchain.flush_batch")
    assert chain.sent and chain.sent[0].startswith("cryptis:v1:batch:")
    proof = (await bob.get(f"/api/messages/{msg['id']}/proof")).json()
    types = {e["event_type"] for e in proof["events"]}
    assert types == {"message.sent", "message.read"}
    for ev in proof["events"]:
        assert ev["status"] == "confirmed" and ev["batch"]["tx_hash"]
        assert merkle.verify(ev["event_hash"], ev["merkle_proof"], ev["batch"]["merkle_root"])
    assert len(chain.sent) == 1  # оба события ушли ОДНОЙ транзакцией


async def test_protobuf_packet_and_signature_checks(make_user):
    alice, bob = await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    packet = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "binary"})
    body = wire.encode("MessagePacket", cc.packet_to_proto_fields(packet))
    r = await alice.post(f"/api/threads/{thread_id}/messages", headers={"Content-Type": "application/x-protobuf"},
                         content=body)
    assert r.status_code == 201, r.text

    # подпись чужим ключом (сервер подменяет содержимое / злоумышленник) — отклоняется
    forged = cc.make_packet(bob.keys, thread_id, conv_key, 1, {"text": "fake"})
    r = await alice.post(f"/api/threads/{thread_id}/messages", forged)
    assert r.status_code == 422 and r.json()["error"] == "bad_signature"

    tampered = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "x"})
    ct = bytearray(base64.b64decode(tampered["ciphertext"]))
    ct[0] ^= 1
    tampered["ciphertext"] = base64.b64encode(bytes(ct)).decode()
    assert (await alice.post(f"/api/threads/{thread_id}/messages", tampered)).status_code == 422

    stale = cc.make_packet(alice.keys, thread_id, conv_key, 5, {"text": "x"})
    r = await alice.post(f"/api/threads/{thread_id}/messages", stale)
    assert r.status_code == 409 and r.json()["error"] == "stale_epoch"


async def test_outsider_cannot_read_or_post(make_user):
    alice, bob, eve = await make_user(), await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    assert (await eve.get(f"/api/threads/{thread_id}/messages")).status_code == 404
    p = cc.make_packet(eve.keys, thread_id, conv_key, 1, {"text": "hi"})
    assert (await eve.post(f"/api/threads/{thread_id}/messages", p)).status_code == 404
    assert (await eve.get(f"/api/threads/{thread_id}/keys")).status_code == 404


async def test_blind_index_search(make_user):
    alice, bob = await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    for text in ("Deploy the new build", "Lunch tomorrow?", "build is green"):
        p = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": text})
        assert (await alice.post(f"/api/threads/{thread_id}/messages", p)).status_code == 201

    def q(*words):
        return "&".join(f"q={cc.search_token(conv_key, w)}" for w in words)

    found = (await bob.get(f"/api/threads/{thread_id}/messages/search?{q('build')}")).json()
    assert sorted(cc.decrypt_message(conv_key, m)["text"] for m in found) == ["Deploy the new build", "build is green"]
    found = (await bob.get(f"/api/threads/{thread_id}/messages/search?{q('bui', 'green')}")).json()
    assert [cc.decrypt_message(conv_key, m)["text"] for m in found] == ["build is green"]
    assert (await bob.get(f"/api/threads/{thread_id}/messages/search?{q('pizza')}")).json() == []


async def test_reply_forward_and_attachments(make_user):
    alice, bob, carol = await make_user(), await make_user(), await make_user()
    t1, k1 = await open_dm(alice, bob)
    t2, k2 = await open_dm(alice, carol)

    blob = os.urandom(24) + b"encrypted-file-bytes"
    r = await alice.client.post("/api/uploads", headers=alice.h, files={"file": ("x.bin", blob)}, data={"kind": "file"})
    assert r.status_code == 201, r.text
    att = r.json()["id"]
    p = cc.make_packet(alice.keys, t1, k1, 1, {"type": "file", "name": "report.pdf"}, kind="file", attachment_id=att)
    msg = (await alice.post(f"/api/threads/{t1}/messages", p)).json()
    got = await bob.get(f"/api/messages/{msg['id']}/attachment")
    assert got.status_code == 200 and got.content == blob
    # вложение нельзя прикрепить второй раз
    p2 = cc.make_packet(alice.keys, t1, k1, 1, {"text": "again"}, attachment_id=att)
    assert (await alice.post(f"/api/threads/{t1}/messages", p2)).status_code == 422

    reply = cc.make_packet(bob.keys, t1, k1, 1, {"text": "thanks"}, reply_to_id=msg["id"])
    assert (await bob.post(f"/api/threads/{t1}/messages", reply)).json()["reply_to_id"] == msg["id"]

    # пересылка: клиент перешифровывает ключом целевого чата
    fwd = cc.make_packet(alice.keys, t2, k2, 1, {"text": "fwd"})
    r = await alice.post(f"/api/messages/{msg['id']}/forward", {"target_thread_id": t2, "packet": fwd})
    assert r.status_code == 201 and r.json()["forwarded_from_id"] == msg["id"]
    # Carol не участник t1 — переслать оттуда в свой чат не может
    fwd2 = cc.make_packet(carol.keys, t2, k2, 1, {"text": "x"})
    assert (await carol.post(f"/api/messages/{msg['id']}/forward", {"target_thread_id": t2, "packet": fwd2})).status_code == 404


async def test_block_prevents_messaging(make_user):
    alice, bob = await make_user("Alice"), await make_user("Bob")
    thread_id, conv_key = await open_dm(alice, bob)
    assert (await bob.post(f"/api/blocklist/{alice.id}")).status_code == 204
    assert [u["id"] for u in (await bob.get("/api/blocklist")).json()] == [alice.id]
    p = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "hi"})
    r = await alice.post(f"/api/threads/{thread_id}/messages", p)
    assert r.status_code == 403 and r.json()["error"] == "blocked"
    assert (await bob.delete(f"/api/blocklist/{alice.id}")).status_code == 204
    assert (await alice.post(f"/api/threads/{thread_id}/messages", p)).status_code == 201


async def test_clear_delete_mute_and_disappearing(make_user, infra):
    alice, bob = await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    p = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "old"})
    await alice.post(f"/api/threads/{thread_id}/messages", p)

    assert (await bob.post(f"/api/threads/{thread_id}/clear")).status_code == 204
    assert (await bob.get(f"/api/threads/{thread_id}/messages")).json()["items"] == []
    assert len((await alice.get(f"/api/threads/{thread_id}/messages")).json()["items"]) == 1

    assert (await bob.delete(f"/api/threads/{thread_id}")).status_code == 204
    assert (await bob.get("/api/threads")).json() == []
    p = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "new"})
    await alice.post(f"/api/threads/{thread_id}/messages", p)
    assert len((await bob.get("/api/threads")).json()) == 1  # новое сообщение возвращает чат

    assert (await bob.post(f"/api/threads/{thread_id}/mute", {"muted": True})).status_code == 204
    assert (await bob.get(f"/api/threads/{thread_id}")).json()["muted"] is True

    assert (await alice.client.patch(f"/api/threads/{thread_id}/disappearing", json={"seconds": 300},
                                     headers=alice.h)).status_code == 204
    p = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "vanish"})
    m = (await alice.post(f"/api/threads/{thread_id}/messages", p)).json()
    await bob.post(f"/api/threads/{thread_id}/read", {})
    items = (await bob.get(f"/api/threads/{thread_id}/messages")).json()["items"]
    vanishing = next(i for i in items if i["id"] == m["id"])
    assert vanishing["expires_at"] is not None


async def test_dm_requires_peer_keys_and_rejects_self(make_user):
    alice = await make_user()
    nokeys = await make_user(with_keys=False)
    r = await alice.post("/api/threads", {"user_id": nokeys.id})
    assert r.status_code == 409 and r.json()["error"] == "peer_no_keys"
    assert (await alice.post("/api/threads", {"user_id": alice.id})).status_code == 422


async def test_offline_recipient_gets_push_without_plaintext(make_user, push, infra):
    alice, bob = await make_user("Alice"), await make_user("Bob")
    await bob.post("/api/me/push-token", {"platform": "fcm", "token": "device-token-123"})
    thread_id, conv_key = await open_dm(alice, bob)
    p = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "secret plan"}, push_preview="New message")
    await alice.post(f"/api/threads/{thread_id}/messages", p)
    assert len(push.sent) == 1
    assert push.sent[0].title == "Alice" and "secret" not in push.sent[0].body


async def test_replying_marks_previous_messages_read(make_user, infra):
    """Регрессия: получатель открыл чат и сразу ответил — отправитель должен увидеть «прочитано»."""
    alice, bob = await make_user(), await make_user()
    thread_id, conv_key = await open_dm(alice, bob)
    await alice.post(f"/api/threads/{thread_id}/messages", cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "q"}))
    await bob.post(f"/api/threads/{thread_id}/messages", cc.make_packet(bob.keys, thread_id, conv_key, 1, {"text": "a"}))
    assert (await alice.get("/api/threads")).json()[0]["peer_last_read_seq"] >= 1
    assert (await bob.get("/api/threads")).json()[0]["unread"] == 0
    infra.tasks.enqueue("blockchain.flush_batch")
    msg_id = (await alice.get(f"/api/threads/{thread_id}/messages")).json()["items"][0]["id"]
    types = {e["event_type"] for e in (await alice.get(f"/api/messages/{msg_id}/proof")).json()["events"]}
    assert "message.read" in types
