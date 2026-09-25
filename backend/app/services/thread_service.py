"""Личные и групповые чаты: список, настройки чата, квитанции доставки и прочтения."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Message, Thread, ThreadMember, User, utcnow
from app.repositories import KeyRepository, MessageRepository, SettingsRepository, ThreadRepository, UserRepository
from app.schemas.threads import MemberOut, ThreadDetailOut, ThreadOut
from app.services import cache
from app.services.chain_service import ChainService
from app.services.errors import Conflict, Forbidden, Invalid, NotFound
from app.services.infra import Infra
from app.services.presenters import avatar_url, message_out, user_public


class ThreadService:
    def __init__(self, session: AsyncSession, infra: Infra) -> None:
        self.s = session
        self.infra = infra
        self.threads = ThreadRepository(session)
        self.users = UserRepository(session)
        self.messages = MessageRepository(session)
        self.settings = SettingsRepository(session)

    # ------------------------------------------------------------------ доступ
    async def require_member(self, thread_id: str, user_id: str) -> tuple[Thread, ThreadMember]:
        thread = await self.threads.get(thread_id)
        member = await self.threads.membership(thread_id, user_id) if thread else None
        if thread is None or member is None or member.left_at is not None:
            raise NotFound("thread not found")
        return thread, member

    # ------------------------------------------------------------------ список
    async def list(self, user: User, filter_: str = "all", query: str = "") -> list[ThreadOut]:
        pairs = await self.threads.list_for_user(user.id)
        visible = [
            (t, m) for t, m in pairs
            if m.hidden_at is None or (t.last_message_at is not None and t.last_message_at > m.hidden_at)
        ]
        items = await self._build(user, visible)
        f = (filter_ or "all").lower()
        if f == "unread":
            items = [i for i in items if i.unread > 0]
        elif f == "direct":
            items = [i for i in items if i.kind == "direct"]
        elif f == "groups":
            items = [i for i in items if i.kind == "group"]
        q = (query or "").strip().lower().lstrip("@")
        if q:
            def match(i: ThreadOut) -> bool:
                hay = [i.title or ""]
                if i.peer:
                    hay += [i.peer.display_name, i.peer.username or ""]
                return any(q in h.lower() for h in hay)
            items = [i for i in items if match(i)]
        return items

    async def _build(self, user: User, pairs: list[tuple[Thread, ThreadMember]]) -> list[ThreadOut]:
        thread_ids = [t.id for t, _ in pairs]
        members = await self.threads.members_for_threads(thread_ids)
        peer_ids = {
            m.user_id for t, _ in pairs if t.kind == "direct" for m in members[t.id] if m.user_id != user.id
        }
        other_ids = {m.user_id for ms in members.values() for m in ms if m.user_id != user.id}
        peers = await self.users.get_many(list(peer_ids))
        wallets = await self.users.primary_wallets(list(peer_ids))
        prefs = await self.settings.get_many(list(other_ids))
        online = await cache.online_users(self.infra.redis, [p for p in peer_ids if prefs[p].show_online])
        last = await self.messages.last_for_threads(thread_ids)

        cached_unread = await cache.get_unread(self.infra.redis, user.id, thread_ids)
        missing = [(t.id, m.last_read_seq, m.cleared_seq) for t, m in pairs if cached_unread.get(t.id) is None]
        fresh_unread = await self.threads.unread_counts(user.id, missing)
        for tid, value in fresh_unread.items():
            await cache.set_unread(self.infra.redis, user.id, tid, value)

        out: list[ThreadOut] = []
        for t, m in pairs:
            others = [x for x in members[t.id] if x.user_id != user.id]
            peer = None
            if t.kind == "direct" and others and others[0].user_id in peers:
                pid = others[0].user_id
                peer = user_public(peers[pid], wallets.get(pid), online=pid in online)
            receipts_on = [x for x in others if prefs[x.user_id].send_read_receipts]
            lm = last.get(t.id)
            if lm is not None and lm.seq <= m.cleared_seq:
                lm = None
            unread = cached_unread.get(t.id)
            if unread is None:
                unread = fresh_unread.get(t.id, 0)
            out.append(ThreadOut(
                id=t.id,
                kind=t.kind,
                title=t.title if t.kind == "group" else (peer.display_name if peer else None),
                avatar_url=avatar_url(t.avatar_upload_id) if t.kind == "group" else (peer.avatar_url if peer else None),
                avatar_tint=t.avatar_tint if t.kind == "group" else (peer.avatar_tint if peer else t.avatar_tint),
                peer=peer,
                members_count=len(members[t.id]),
                my_role=m.role,
                muted=m.muted,
                unread=unread,
                last_message=message_out(lm) if lm else None,
                last_message_at=t.last_message_at,
                created_at=t.created_at,
                disappearing_seconds=t.disappearing_seconds,
                screenshot_block=t.screenshot_block,
                key_epoch=t.key_epoch,
                rotation_pending=t.rotation_pending,
                last_read_seq=m.last_read_seq,
                peer_last_read_seq=min((x.last_read_seq for x in receipts_on), default=0),
                peer_last_delivered_seq=min((x.last_delivered_seq for x in others), default=0),
            ))
        return out

    async def detail(self, user: User, thread_id: str) -> ThreadDetailOut:
        thread, member = await self.require_member(thread_id, user.id)
        [base] = await self._build(user, [(thread, member)])
        members = await self.threads.active_members(thread_id)
        users = await self.users.get_many([m.user_id for m in members])
        wallets = await self.users.primary_wallets([m.user_id for m in members])
        return ThreadDetailOut(
            **base.model_dump(),
            members=[
                MemberOut(user=user_public(users[m.user_id], wallets.get(m.user_id)), role=m.role, joined_at=m.joined_at)
                for m in members if m.user_id in users
            ],
        )

    # ------------------------------------------------------------------ личный чат
    async def open_direct(self, user: User, peer_id: str) -> ThreadDetailOut:
        if peer_id == user.id:
            raise Invalid("cannot start a conversation with yourself")
        peer = await self.users.get(peer_id)
        if peer is None or peer.is_suspended:
            raise NotFound("user not found")
        if await self.settings.is_blocked_between(user.id, peer_id):
            raise Forbidden("messaging is blocked between these users", code="blocked")
        key = ":".join(sorted([user.id, peer_id]))
        thread = await self.threads.by_direct_key(key)
        if thread is None:
            if await KeyRepository(self.s).active_set(peer_id) is None:
                raise Conflict("this user has not set up encryption keys yet", code="peer_no_keys")
            thread = Thread(kind="direct", direct_key=key, created_by=user.id)
            self.s.add(thread)
            await self.s.flush()
            self.s.add_all([
                ThreadMember(thread_id=thread.id, user_id=user.id, role="member"),
                ThreadMember(thread_id=thread.id, user_id=peer_id, role="member"),
            ])
        else:
            me = await self.threads.membership(thread.id, user.id)
            if me is not None:
                me.hidden_at = None
        await self.s.commit()
        return await self.detail(user, thread.id)

    # ------------------------------------------------------------------ действия
    async def hide(self, user: User, thread_id: str) -> None:
        thread, member = await self.require_member(thread_id, user.id)
        member.hidden_at = utcnow()
        member.cleared_seq = thread.last_seq
        member.last_read_seq = max(member.last_read_seq, thread.last_seq)
        await self.s.commit()
        await cache.reset_unread(self.infra.redis, user.id, thread_id)

    async def clear(self, user: User, thread_id: str) -> None:
        thread, member = await self.require_member(thread_id, user.id)
        member.cleared_seq = thread.last_seq
        member.last_read_seq = max(member.last_read_seq, thread.last_seq)
        await self.s.commit()
        await cache.reset_unread(self.infra.redis, user.id, thread_id)

    async def mute(self, user: User, thread_id: str, muted: bool) -> None:
        _, member = await self.require_member(thread_id, user.id)
        member.muted = muted
        await self.s.commit()

    async def advance_read(self, thread: Thread, member: ThreadMember, user: User, target: int,
                           chain: ChainService) -> bool:
        """Сдвинуть отметку прочтения участника. True — нужно разослать квитанцию message.read."""
        previous = member.last_read_seq
        if target <= previous:
            return False
        member.last_read_seq = target
        member.last_delivered_seq = max(member.last_delivered_seq, target)
        now = utcnow()
        if thread.disappearing_seconds:
            # «After messages in this chat get read, they will vanish after the selected time»
            await self.s.execute(
                update(Message)
                .where(Message.thread_id == thread.id, Message.seq > previous, Message.seq <= target,
                       Message.sender_id != user.id, Message.expires_at.is_(None))
                .values(expires_at=now + timedelta(seconds=thread.disappearing_seconds))
            )
        if (await self.settings.get(user.id)).send_read_receipts:
            chain.record_receipt("message.read", thread.id, user.id, target, now)
            return True
        return False

    async def publish_read(self, thread_id: str, user_id: str, up_to_seq: int) -> None:
        for uid in await self.threads.active_member_ids(thread_id):
            if uid != user_id:
                await self.infra.bus.publish(uid, "message.read",
                                             {"thread_id": thread_id, "user_id": user_id, "up_to_seq": up_to_seq})

    async def mark_read(self, user: User, thread_id: str, up_to_seq: int | None) -> int:
        thread, member = await self.require_member(thread_id, user.id)
        target = thread.last_seq if up_to_seq is None else min(up_to_seq, thread.last_seq)
        chain = ChainService(self.s, self.infra)
        notify = await self.advance_read(thread, member, user, target, chain)
        await self.s.commit()
        await chain.after_commit()
        await cache.reset_unread(self.infra.redis, user.id, thread_id)
        if notify:
            await self.publish_read(thread_id, user.id, target)
        return member.last_read_seq

    async def mark_delivered(self, user_id: str, thread_id: str, up_to_seq: int) -> None:
        thread = await self.threads.get(thread_id)
        member = await self.threads.membership(thread_id, user_id) if thread else None
        if thread is None or member is None or member.left_at is not None:
            return
        target = min(up_to_seq, thread.last_seq)
        if target <= member.last_delivered_seq:
            return
        member.last_delivered_seq = target
        chain = ChainService(self.s, self.infra)
        chain.record_receipt("message.delivered", thread_id, user_id, target)
        await self.s.commit()
        await chain.after_commit()
        for uid in await self.threads.active_member_ids(thread_id):
            if uid != user_id:
                await self.infra.bus.publish(uid, "message.delivered",
                                             {"thread_id": thread_id, "user_id": user_id, "up_to_seq": target})

    async def _require_can_manage(self, thread: Thread, member: ThreadMember) -> None:
        if thread.kind == "group" and member.role not in ("owner", "admin"):
            raise Forbidden("only group admins can change this setting")

    async def set_disappearing(self, user: User, thread_id: str, seconds: int | None) -> None:
        thread, member = await self.require_member(thread_id, user.id)
        await self._require_can_manage(thread, member)
        thread.disappearing_seconds = seconds or None
        await self.s.commit()
        await self._notify_updated(thread_id, {"disappearing_seconds": thread.disappearing_seconds})

    async def set_screenshot_block(self, user: User, thread_id: str, enabled: bool) -> None:
        thread, member = await self.require_member(thread_id, user.id)
        await self._require_can_manage(thread, member)
        thread.screenshot_block = enabled
        await self.s.commit()
        await self._notify_updated(thread_id, {"screenshot_block": enabled})

    async def _notify_updated(self, thread_id: str, data: dict) -> None:
        for uid in await self.threads.active_member_ids(thread_id):
            await self.infra.bus.publish(uid, "thread.updated", {"thread_id": thread_id, **data})
