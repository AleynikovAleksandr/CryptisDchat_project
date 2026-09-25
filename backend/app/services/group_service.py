"""Групповые чаты (ТЗ 2, 6.8; Gunicorn_Celery.md 4.4).

Порядок при изменении состава:
  1. изменение сохраняется в БД и получает хэш события;
  2. Celery (очередь blockchain) отправляет транзакцию служебного кошелька и ждёт подтверждения;
  3. только после подтверждения Celery применяет изменение листа в дереве ключей и рассылает
     участникам `key.rotation` — клиенты перешифровывают затронутую ветку (см. KeyService).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Attachment,
    GroupMembershipEvent,
    Thread,
    ThreadMember,
    User,
    new_uuid,
    utcnow,
)
from app.repositories import KeyRepository, SettingsRepository, ThreadRepository, UserRepository
from app.schemas.threads import GroupCreateIn, GroupPatchIn, MemberOut, ThreadDetailOut
from app.services import event_hash, treekem
from app.services.errors import Conflict, Forbidden, Invalid, NotFound
from app.services.infra import Infra
from app.services.thread_service import ThreadService

ANCHOR_TASK = "blockchain.anchor_membership"


class GroupService:
    def __init__(self, session: AsyncSession, infra: Infra) -> None:
        self.s = session
        self.infra = infra
        self.threads = ThreadRepository(session)
        self.users = UserRepository(session)
        self.settings = SettingsRepository(session)
        self.keys = KeyRepository(session)
        self.thread_svc = ThreadService(session, infra)

    async def _require_group(self, user: User, group_id: str) -> tuple[Thread, ThreadMember]:
        thread, member = await self.thread_svc.require_member(group_id, user.id)
        if thread.kind != "group":
            raise NotFound("group not found")
        return thread, member

    @staticmethod
    def _require_admin(member: ThreadMember) -> None:
        if member.role not in ("owner", "admin"):
            raise Forbidden("only group admins can do this")

    async def _check_invitees(self, inviter: User, user_ids: list[str]) -> list[User]:
        ids = list(dict.fromkeys(u for u in user_ids if u != inviter.id))
        if not ids:
            raise Invalid("add at least one other member")
        users = await self.users.get_many(ids)
        prefs = await self.settings.get_many(ids)
        keysets = await self.keys.active_sets(ids)
        result: list[User] = []
        for uid in ids:
            u = users.get(uid)
            if u is None or u.is_suspended:
                raise NotFound(f"user {uid} not found")
            if await self.settings.is_blocked_between(inviter.id, uid):
                raise Forbidden(f"{u.display_name} can't be added", code="blocked", extra={"user_id": uid})
            policy = prefs[uid].group_invite_policy
            if policy == "nobody" or (
                policy == "allowlist" and not await self.settings.is_allowed_inviter(uid, inviter.id)
            ):
                raise Forbidden(f"{u.display_name} doesn't accept group invites", code="invite_not_allowed",
                                extra={"user_id": uid})
            if uid not in keysets:
                raise Conflict(f"{u.display_name} has not set up encryption keys yet", code="peer_no_keys",
                               extra={"user_id": uid})
            result.append(u)
        return result

    def _event(self, thread: Thread, actor: User, action: str, subject: str | None,
               leaf: int | None) -> GroupMembershipEvent:
        eid = new_uuid()
        now = utcnow()
        ev = GroupMembershipEvent(
            id=eid, thread_id=thread.id, actor_id=actor.id, action=action, subject_user_id=subject,
            leaf_index=leaf, created_at=now,
            event_hash=event_hash.membership_hash(eid, thread.id, actor.id, action, subject, now),
        )
        self.s.add(ev)
        return ev

    async def _validate_avatar(self, user: User, upload_id: str | None) -> None:
        if upload_id is None:
            return
        att = await self.s.get(Attachment, upload_id)
        if att is None or att.owner_id != user.id or att.kind != "group_avatar":
            raise Invalid("group avatar upload not found")

    # ------------------------------------------------------------------ создание
    async def create(self, user: User, body: GroupCreateIn) -> ThreadDetailOut:
        if await self.keys.active_set(user.id) is None:
            raise Conflict("set up your encryption keys first", code="no_keys")
        invitees = await self._check_invitees(user, body.member_ids)
        await self._validate_avatar(user, body.avatar_upload_id)
        member_ids = [user.id] + [u.id for u in invitees]
        thread = Thread(kind="group", title=body.title.strip(), created_by=user.id,
                        avatar_upload_id=body.avatar_upload_id,
                        tree_leaf_capacity=treekem.capacity_for(len(member_ids)))
        self.s.add(thread)
        await self.s.flush()
        for leaf, uid in enumerate(member_ids):
            self.s.add(ThreadMember(thread_id=thread.id, user_id=uid, role="owner" if uid == user.id else "member",
                                    leaf_index=leaf))
        ev = self._event(thread, user, "create", None, None)
        await self.s.commit()
        self.infra.tasks.enqueue(ANCHOR_TASK, ev.id)
        for uid in member_ids:
            await self.infra.bus.publish(uid, "thread.updated", {"thread_id": thread.id, "created": True})
        return await self.thread_svc.detail(user, thread.id)

    # ------------------------------------------------------------------ состав
    async def members(self, user: User, group_id: str) -> list[MemberOut]:
        await self._require_group(user, group_id)
        return (await self.thread_svc.detail(user, group_id)).members

    async def _free_leaf(self, thread: Thread) -> int:
        active = await self.threads.active_members(thread.id)
        taken = {m.leaf_index for m in active if m.leaf_index is not None}
        nodes = await self.threads.tree_nodes(thread.id)
        pending = await self.s.scalars(
            select(GroupMembershipEvent.leaf_index).where(
                GroupMembershipEvent.thread_id == thread.id, GroupMembershipEvent.rotation_status != "done",
                GroupMembershipEvent.leaf_index.is_not(None),
            )
        )
        taken |= {x for x in pending if x is not None}
        for leaf in range(thread.tree_leaf_capacity):
            node = nodes.get(treekem.leaf_node(leaf))
            if leaf not in taken and (node is None or node.public_key is None):
                return leaf
        leaf = thread.tree_leaf_capacity
        thread.tree_leaf_capacity = treekem.capacity_for(leaf + 1)
        return leaf

    async def add_members(self, user: User, group_id: str, user_ids: list[str]) -> list[MemberOut]:
        thread, member = await self._require_group(user, group_id)
        self._require_admin(member)
        thread = await self.threads.get(group_id, for_update=True)
        assert thread is not None
        invitees = await self._check_invitees(user, user_ids)
        events: list[GroupMembershipEvent] = []
        for invitee in invitees:
            existing = await self.threads.membership(group_id, invitee.id)
            if existing is not None and existing.left_at is None:
                continue
            leaf = await self._free_leaf(thread)
            if existing is None:
                existing = ThreadMember(thread_id=group_id, user_id=invitee.id, role="member")
                self.s.add(existing)
            existing.left_at = None
            existing.role = "member"
            existing.joined_at = utcnow()
            existing.leaf_index = leaf
            # новый участник не получает ключей прошлых эпох — старую историю он прочитать не сможет
            existing.cleared_seq = thread.last_seq
            existing.last_read_seq = thread.last_seq
            existing.joined_seq = thread.last_seq
            existing.hidden_at = None
            await self.s.flush()
            events.append(self._event(thread, user, "add", invitee.id, leaf))
        await self.s.commit()
        for ev in events:
            self.infra.tasks.enqueue(ANCHOR_TASK, ev.id)
        await self._notify(group_id, {"members_changed": True})
        return await self.members(user, group_id)

    async def remove_member(self, user: User, group_id: str, target_id: str) -> None:
        thread, member = await self._require_group(user, group_id)
        leaving = target_id == user.id
        if not leaving:
            self._require_admin(member)
        target = await self.threads.membership(group_id, target_id)
        if target is None or target.left_at is not None:
            raise NotFound("member not found")
        if target.role == "owner" and not leaving:
            raise Forbidden("the owner can't be removed")
        notify_ids = await self.threads.active_member_ids(group_id)
        target.left_at = utcnow()
        if target.role == "owner":
            rest = [m for m in await self.threads.active_members(group_id) if m.user_id != target_id]
            if rest:
                heir = next((m for m in rest if m.role == "admin"), rest[0])
                heir.role = "owner"
        ev = self._event(thread, user, "leave" if leaving else "remove", target_id, target.leaf_index)
        target.leaf_index = None
        await self.s.commit()
        self.infra.tasks.enqueue(ANCHOR_TASK, ev.id)
        for uid in notify_ids:
            await self.infra.bus.publish(uid, "thread.updated", {"thread_id": group_id, "members_changed": True,
                                                                 "removed_user_id": target_id})

    async def patch(self, user: User, group_id: str, body: GroupPatchIn) -> ThreadDetailOut:
        thread, member = await self._require_group(user, group_id)
        self._require_admin(member)
        if body.title is not None:
            thread.title = body.title.strip()
        if body.avatar_upload_id is not None:
            await self._validate_avatar(user, body.avatar_upload_id)
            thread.avatar_upload_id = body.avatar_upload_id
        await self.s.commit()
        await self._notify(group_id, {"title": thread.title})
        return await self.thread_svc.detail(user, group_id)

    async def _notify(self, group_id: str, data: dict) -> None:
        for uid in await self.threads.active_member_ids(group_id):
            await self.infra.bus.publish(uid, "thread.updated", {"thread_id": group_id, **data})
