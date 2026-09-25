"""Сообщения: приём E2E-пакета, история, поиск по слепому индексу, пересылка, вложения.

Сервер работает только с шифротекстом (ТЗ 7): в этом модуле нет ни одного пути,
который мог бы получить открытый текст — ключей для этого у сервера просто нет.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Attachment, Message, Thread, User, new_uuid, utcnow
from app.repositories import (
    ChainRepository,
    KeyRepository,
    MessageRepository,
    SettingsRepository,
    ThreadRepository,
    UserRepository,
)
from app.schemas.messages import MessageOut, MessageProofOut, MessagesPageOut, SendMessageIn, UploadOut
from app.services import cache, signatures
from app.services.chain_service import ChainService
from app.services.errors import Conflict, Forbidden, Invalid, NotFound
from app.services.infra import Infra
from app.services.presenters import message_out
from app.services.thread_service import ThreadService

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
PUSH_TASK = "notifications.send_push"


def _b64(value: str, field: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Invalid(f"{field}: invalid base64") from exc


class MessageService:
    def __init__(self, session: AsyncSession, infra: Infra) -> None:
        self.s = session
        self.infra = infra
        self.threads = ThreadRepository(session)
        self.messages = MessageRepository(session)
        self.keys = KeyRepository(session)
        self.thread_svc = ThreadService(session, infra)

    # ------------------------------------------------------------------ отправка
    async def send(self, user: User, thread_id: str, packet: SendMessageIn) -> MessageOut:
        await self.thread_svc.require_member(thread_id, user.id)

        existing = await self.messages.by_client_id(user.id, packet.client_msg_id)
        if existing is not None:
            if existing.thread_id != thread_id:
                raise Conflict("client_msg_id already used", code="duplicate_client_msg_id")
            return message_out(existing)  # повторная отправка после обрыва связи — идемпотентно

        thread = await self.threads.get(thread_id, for_update=True)
        assert thread is not None
        await self._check_can_post(user, thread)

        nonce = _b64(packet.nonce, "nonce")
        ciphertext = _b64(packet.ciphertext, "ciphertext")
        signature = _b64(packet.signature, "signature")
        if len(nonce) != 24:
            raise Invalid("nonce must be 24 bytes (XSalsa20)")
        if len(ciphertext) < 16:
            raise Invalid("ciphertext too short")

        if thread.key_epoch == 0:
            raise Conflict("conversation key is not established yet", code="no_conversation_key")
        if packet.key_epoch not in (thread.key_epoch, thread.key_epoch - 1):
            raise Conflict("stale key epoch", code="stale_epoch", extra={"current_epoch": thread.key_epoch})

        keyset = await self.keys.set_version(user.id, packet.sender_key_version)
        if keyset is None:
            raise Invalid("unknown sender key version")
        data = signatures.signing_bytes(thread_id, packet.client_msg_id, packet.key_epoch, nonce, ciphertext)
        if not signatures.verify_p1363(keyset.signing_pub, signature, data):
            raise Invalid("bad message signature", code="bad_signature")

        if packet.reply_to_id:
            reply = await self.messages.get(packet.reply_to_id)
            if reply is None or reply.thread_id != thread_id:
                raise Invalid("reply_to_id is not in this thread")
        if packet.forwarded_from_id:
            await self._require_readable(user, packet.forwarded_from_id)

        attachment: Attachment | None = None
        if packet.attachment_id:
            attachment = await self.messages.get_attachment(packet.attachment_id)
            if (attachment is None or attachment.owner_id != user.id or attachment.deleted_at is not None
                    or attachment.message_id is not None or attachment.kind != "file"):
                raise Invalid("attachment is not available")

        tokens = [t for t in packet.search_tokens if TOKEN_RE.match(t)]
        seq = await self.threads.next_seq(thread)
        now = utcnow()
        msg = Message(
            id=new_uuid(), thread_id=thread_id, seq=seq, sender_id=user.id,
            client_msg_id=packet.client_msg_id, kind=packet.kind,
            ciphertext=ciphertext, nonce=nonce, signature=signature,
            key_epoch=packet.key_epoch, sender_key_version=packet.sender_key_version,
            content_hash=hashlib.sha256(ciphertext).hexdigest(),
            reply_to_id=packet.reply_to_id, forwarded_from_id=packet.forwarded_from_id,
            attachment_id=packet.attachment_id, created_at=now,
        )
        self.s.add(msg)
        await self.s.flush()
        await self.messages.add_search_tokens(msg, tokens)
        if attachment is not None:
            attachment.message_id = msg.id
            attachment.thread_id = thread_id
        thread.last_message_at = now
        chain = ChainService(self.s, self.infra)
        chain.record_sent(msg)
        # отвечая, отправитель тем самым прочитал всё, что пришло до его сообщения
        read_receipt = False
        sender_member = await self.threads.membership(thread_id, user.id)
        if sender_member is not None:
            read_receipt = await self.thread_svc.advance_read(thread, sender_member, user, seq - 1, chain)
            sender_member.last_read_seq = seq
            sender_member.last_delivered_seq = seq
            sender_member.hidden_at = None
        await self.s.commit()
        await chain.after_commit()

        out = message_out(msg)
        await self._fan_out(user, thread, out, packet.push_preview)
        if read_receipt:
            await cache.reset_unread(self.infra.redis, user.id, thread_id)
            await self.thread_svc.publish_read(thread_id, user.id, seq - 1)
        return out

    async def _check_can_post(self, user: User, thread: Thread) -> None:
        if thread.kind == "direct":
            peer_ids = [u for u in await self.threads.active_member_ids(thread.id) if u != user.id]
            settings = SettingsRepository(self.s)
            for pid in peer_ids:
                if await settings.is_blocked_between(user.id, pid):
                    raise Forbidden("messaging is blocked between these users", code="blocked")

    async def _fan_out(self, sender: User, thread: Thread, out: MessageOut, push_preview: str | None) -> None:
        """Pub/Sub-«звоночек» подключённым клиентам + push тем, кто офлайн (Gunicorn_Celery.md 3.2, 4.6)."""
        members = await self.threads.active_members(thread.id)
        recipients = [m for m in members if m.user_id != sender.id]
        online = await cache.online_users(self.infra.redis, [m.user_id for m in recipients])
        payload = out.model_dump(mode="json")
        for m in members:
            await self.infra.bus.publish(m.user_id, "message.new", payload)
        for m in recipients:
            await cache.incr_unread(self.infra.redis, m.user_id, thread.id)
            if m.user_id not in online and not m.muted:
                title = sender.display_name or "CryptisDchat"
                if thread.kind == "group" and thread.title:
                    title = f"{title} · {thread.title}"
                self.infra.tasks.enqueue(
                    PUSH_TASK, m.user_id, title, push_preview or "New message",
                    {"thread_id": thread.id, "message_id": out.id},
                )
        await cache.invalidate_threads(self.infra.redis, *[m.user_id for m in members])

    # ------------------------------------------------------------------ чтение
    async def _require_readable(self, user: User, message_id: str) -> Message:
        msg = await self.messages.get(message_id)
        if msg is None:
            raise NotFound("message not found")
        _, member = await self.thread_svc.require_member(msg.thread_id, user.id)
        if msg.seq <= member.cleared_seq:
            raise NotFound("message not found")
        return msg

    async def page(self, user: User, thread_id: str, before: int | None, limit: int) -> MessagesPageOut:
        _, member = await self.thread_svc.require_member(thread_id, user.id)
        limit = max(1, min(limit, 100))
        items = await self.messages.page(thread_id, before=before, after_seq=member.cleared_seq, limit=limit)
        now = utcnow()
        items = [m for m in items if m.expires_at is None or m.expires_at > now]
        next_before = items[0].seq if len(items) == limit and items and items[0].seq > 1 else None
        confirmed = await ChainRepository(self.s).confirmed_message_ids([m.id for m in items])
        return MessagesPageOut(items=[message_out(m, m.id in confirmed) for m in items], next_before=next_before)

    async def search(self, user: User, thread_id: str, token_groups: list[list[str]]) -> list[MessageOut]:
        _, member = await self.thread_svc.require_member(thread_id, user.id)
        groups = [[t for t in g if TOKEN_RE.match(t)][:32] for g in token_groups[:8]]
        groups = [g for g in groups if g]
        found = await self.messages.search(thread_id, groups, after_seq=member.cleared_seq)
        return [message_out(m) for m in found]

    async def forward(self, user: User, message_id: str, target_thread_id: str, packet: SendMessageIn) -> MessageOut:
        await self._require_readable(user, message_id)
        packet = packet.model_copy(update={"forwarded_from_id": message_id})
        return await self.send(user, target_thread_id, packet)

    async def proof(self, user: User, message_id: str) -> MessageProofOut:
        msg = await self._require_readable(user, message_id)
        return await ChainService(self.s, self.infra).proof_for(msg)

    # ------------------------------------------------------------------ вложения
    async def upload(self, user: User, data: bytes, kind: str, content_type: str) -> UploadOut:
        if kind not in ("file", "thumbnail", "avatar", "group_avatar"):
            raise Invalid("unknown upload kind")
        if len(data) > self.infra.settings.max_upload_bytes:
            raise Invalid("file is too large", code="too_large")
        if kind in ("avatar", "group_avatar"):
            if content_type not in ("image/png", "image/jpeg", "image/webp", "image/gif") or len(data) > 2 * 1024 * 1024:
                raise Invalid("avatar must be an image up to 2 MB")
        else:
            content_type = "application/octet-stream"  # зашифрованный блоб: тип содержимого серверу неизвестен
        att_id = new_uuid()
        path = await self.infra.storage.save(att_id, data)
        self.s.add(Attachment(id=att_id, owner_id=user.id, kind=kind, storage_path=path,
                              size_bytes=len(data), content_type=content_type))
        await self.s.commit()
        return UploadOut(id=att_id, size=len(data), kind=kind)

    async def attachment_for_message(self, user: User, message_id: str) -> tuple[bytes, str]:
        msg = await self._require_readable(user, message_id)
        if not msg.attachment_id:
            raise NotFound("message has no attachment")
        att = await self.messages.get_attachment(msg.attachment_id)
        if att is None or att.deleted_at is not None:
            raise NotFound("attachment was deleted", code="attachment_deleted")
        return await self.infra.storage.read(att.storage_path), att.content_type

    async def avatar(self, upload_id: str) -> tuple[bytes, str]:
        att = await self.messages.get_attachment(upload_id)
        if att is None or att.deleted_at is not None or att.kind not in ("avatar", "group_avatar"):
            raise NotFound("avatar not found")
        return await self.infra.storage.read(att.storage_path), att.content_type

    async def set_my_avatar(self, user: User, upload_id: str) -> None:
        att = await self.messages.get_attachment(upload_id)
        if att is None or att.owner_id != user.id or att.kind != "avatar":
            raise Invalid("avatar upload not found")
        user.avatar_upload_id = upload_id
        await self.s.commit()
        await self._broadcast_profile(user)

    async def _broadcast_profile(self, user: User) -> None:
        pairs = await self.threads.list_for_user(user.id)
        members = await self.threads.members_for_threads([t.id for t, _ in pairs])
        seen: set[str] = set()
        for ms in members.values():
            for m in ms:
                if m.user_id not in seen:
                    seen.add(m.user_id)
                    await self.infra.bus.publish(m.user_id, "thread.updated", {"profile_user_id": user.id})

    async def cached_media_bytes(self, user: User) -> int:
        return await self.messages.cached_media_bytes(user.id)


async def update_profile(session: AsyncSession, infra: Infra, user: User, *, display_name: str | None,
                         username: str | None, bio: str | None) -> None:
    users = UserRepository(session)
    if username is not None:
        if username == "":
            user.username = None
        else:
            other = await users.by_username(username)
            if other is not None and other.id != user.id:
                raise Conflict("username is taken", code="username_taken")
            user.username = username
    if display_name is not None:
        user.display_name = display_name.strip()
    if bio is not None:
        user.bio = bio
    await session.commit()
    await MessageService(session, infra)._broadcast_profile(user)
