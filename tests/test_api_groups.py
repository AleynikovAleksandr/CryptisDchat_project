"""Группы: состав → транзакция в TON → ротация ключей по TreeKEM-подобной схеме (ТЗ 2, 6.8).

Тест играет роль клиентов: коммитер строит обновление ветки, каждый участник
обрабатывает коммит своими приватными ключами — и все обязаны получить
ОДИН И ТОТ ЖЕ групповой ключ эпохи, а исключённый участник — не получить его.
"""
from __future__ import annotations

from app.services import treekem
from tests import client_crypto as cc


async def commit_pending(committer, group_id: str, periodic: bool = False) -> bytes:
    suffix = "?periodic=true" if periodic else ""
    plan = await committer.get(f"/api/groups/{group_id}/keys/plan{suffix}")
    assert plan.status_code == 200, plan.text
    body, secret = cc.build_commit(plan.json())
    r = await committer.post(f"/api/groups/{group_id}/keys/commit{suffix}", body)
    assert r.status_code == 201, r.text
    return secret


async def sync(user, member: cc.TreeMember, group_id: str, since: int = 0) -> bytes | None:
    commits = (await user.get(f"/api/groups/{group_id}/keys/commits?since={since}")).json()
    secret = None
    for c in commits:
        if c["my_leaf"] is not None:
            member.set_leaf(c["my_leaf"])
        secret = cc.process_commit(member, c)
    return secret


async def test_group_lifecycle_with_key_tree(make_user, chain, infra):
    owner, bob, carol = await make_user("Owner"), await make_user("Bob"), await make_user("Carol")
    r = await owner.post("/api/groups", {"title": "Design crew", "member_ids": [bob.id, carol.id]})
    assert r.status_code == 201, r.text
    group = r.json()
    gid = group["id"]
    assert group["kind"] == "group" and group["members_count"] == 3

    # состав зафиксирован в TON до ротации ключей (Gunicorn_Celery.md 4.4)
    assert any(c.startswith(f"cryptis:v1:group:{gid}:") for c in chain.sent)
    detail = (await owner.get(f"/api/threads/{gid}")).json()
    assert detail["rotation_pending"] is True and detail["key_epoch"] == 0

    # пока ключ группы не установлен — писать нельзя
    early = cc.make_packet(owner.keys, gid, b"\x00" * 32, 1, {"text": "x"})
    assert (await owner.post(f"/api/threads/{gid}/messages", early)).json()["error"] == "no_conversation_key"

    # первичная инициализация дерева целиком
    secret1 = await commit_pending(owner, gid)
    members = {u.id: cc.TreeMember(identity=u.keys.identity) for u in (owner, bob, carol)}
    for u in (owner, bob, carol):
        assert await sync(u, members[u.id], gid) == secret1

    msg = cc.make_packet(bob.keys, gid, secret1, 1, {"text": "hello group"})
    assert (await bob.post(f"/api/threads/{gid}/messages", msg)).status_code == 201
    got = (await carol.get(f"/api/threads/{gid}/messages")).json()["items"][0]
    assert cc.decrypt_message(secret1, got)["text"] == "hello group"

    # добавление участника: обновляется только ветка нового листа
    dave = await make_user("Dave")
    r = await owner.post(f"/api/groups/{gid}/members", {"user_ids": [dave.id]})
    assert r.status_code == 200 and len(r.json()) == 4
    plan = (await carol.get(f"/api/groups/{gid}/keys/plan")).json()
    assert plan["reason"] == "add" and len(plan["path"]) == 2   # log2(4) узла, а не все
    body, secret2 = cc.build_commit(plan)
    assert (await carol.post(f"/api/groups/{gid}/keys/commit", body)).status_code == 201  # коммитит любой участник
    members[dave.id] = cc.TreeMember(identity=dave.keys.identity)
    for u in (owner, bob, carol):
        assert await sync(u, members[u.id], gid, since=1) == secret2
    assert await sync(dave, members[dave.id], gid) == secret2
    # новый участник не видит историю до вступления (ключей прошлой эпохи у него нет)
    assert (await dave.get(f"/api/threads/{gid}/messages")).json()["items"] == []

    # исключение: у Bob остаются старые ключи, но новый ключ эпохи ему недоступен
    assert (await owner.delete(f"/api/groups/{gid}/members/{bob.id}")).status_code == 204
    secret3 = await commit_pending(owner, gid)
    bob_view = cc.TreeMember(identity=bob.keys.identity, privs=dict(members[bob.id].privs))
    commits = (await owner.get(f"/api/groups/{gid}/keys/commits?since=2")).json()
    assert cc.process_commit(bob_view, commits[0]) is None
    for u in (owner, carol, dave):
        assert await sync(u, members[u.id], gid, since=2) == secret3
    assert (await bob.get(f"/api/threads/{gid}/messages")).status_code == 404

    # периодическая ротация своей ветки
    secret4 = await commit_pending(dave, gid, periodic=True)
    for u in (owner, carol, dave):
        assert await sync(u, members[u.id], gid, since=3) == secret4
    assert len({secret1, secret2, secret3, secret4}) == 4


async def test_stale_or_mismatched_commit_is_rejected(make_user):
    owner, bob = await make_user(), await make_user()
    gid = (await owner.post("/api/groups", {"title": "G", "member_ids": [bob.id]})).json()["id"]
    plan = (await owner.get(f"/api/groups/{gid}/keys/plan")).json()
    body, _ = cc.build_commit(plan)
    body["nodes"][0]["ciphertexts"] = body["nodes"][0]["ciphertexts"][:1]
    r = await owner.post(f"/api/groups/{gid}/keys/commit", body)
    assert r.status_code == 409 and r.json()["error"] == "plan_changed"
    await commit_pending(owner, gid)
    body, _ = cc.build_commit(plan)  # тот же план второй раз — эпоха уже занята
    assert (await bob.post(f"/api/groups/{gid}/keys/commit", body)).json()["error"] == "stale_epoch"
    assert (await bob.get(f"/api/groups/{gid}/keys/plan")).json()["error"] == "nothing_to_rotate"


async def test_group_invite_policies(make_user):
    owner, shy, picky, friend = await make_user(), await make_user(), await make_user(), await make_user()
    await shy.patch("/api/settings", {"group_invite_policy": "nobody"})
    await picky.patch("/api/settings", {"group_invite_policy": "allowlist"})
    r = await owner.post("/api/groups", {"title": "G", "member_ids": [shy.id]})
    assert r.status_code == 403 and r.json()["error"] == "invite_not_allowed"
    r = await owner.post("/api/groups", {"title": "G", "member_ids": [picky.id]})
    assert r.status_code == 403
    assert (await picky.post(f"/api/settings/group-invite-allowlist/{owner.id}")).status_code == 204
    assert [u["id"] for u in (await picky.get("/api/settings/group-invite-allowlist")).json()] == [owner.id]
    r = await owner.post("/api/groups", {"title": "G", "member_ids": [picky.id, friend.id]})
    assert r.status_code == 201


async def test_only_admins_manage_members(make_user):
    owner, bob, carol = await make_user(), await make_user(), await make_user()
    gid = (await owner.post("/api/groups", {"title": "G", "member_ids": [bob.id]})).json()["id"]
    assert (await bob.post(f"/api/groups/{gid}/members", {"user_ids": [carol.id]})).status_code == 403
    assert (await bob.patch(f"/api/groups/{gid}", {"title": "hacked"})).status_code == 403
    assert (await owner.patch(f"/api/groups/{gid}", {"title": "Renamed"})).json()["title"] == "Renamed"
    # участник может выйти сам; владелец при выходе передаёт права
    assert (await bob.delete(f"/api/groups/{gid}/members/{bob.id}")).status_code == 204
    assert (await owner.delete(f"/api/groups/{gid}/members/{owner.id}")).status_code == 204


async def test_tree_grows_when_capacity_is_exhausted(make_user):
    users = [await make_user() for _ in range(3)]
    owner = users[0]
    gid = (await owner.post("/api/groups", {"title": "G", "member_ids": [users[1].id]})).json()["id"]
    await commit_pending(owner, gid)
    await owner.post(f"/api/groups/{gid}/members", {"user_ids": [users[2].id]})
    plan = (await owner.get(f"/api/groups/{gid}/keys/plan")).json()
    assert plan["capacity"] == 4 and plan["root"] == treekem.root(4)
