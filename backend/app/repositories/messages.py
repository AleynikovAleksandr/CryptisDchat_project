from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Attachment, Message, MessageSearchToken


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, message_id: str) -> Message | None:
        return await self.s.get(Message, message_id)

    async def by_client_id(self, sender_id: str, client_msg_id: str) -> Message | None:
        return await self.s.scalar(
            select(Message).where(Message.sender_id == sender_id, Message.client_msg_id == client_msg_id)
        )

    async def page(self, thread_id: str, *, before: int | None, after_seq: int, limit: int) -> list[Message]:
        """Курсорная пагинация по seq (ТЗ 6.4): сообщения старше `before`, не старше `after_seq`."""
        stmt = select(Message).where(Message.thread_id == thread_id, Message.seq > after_seq)
        if before is not None:
            stmt = stmt.where(Message.seq < before)
        rows = await self.s.scalars(stmt.order_by(Message.seq.desc()).limit(limit))
        return list(reversed(list(rows)))

    async def last_for_threads(self, thread_ids: list[str]) -> dict[str, Message]:
        if not thread_ids:
            return {}
        sub = (
            select(Message.thread_id, func.max(Message.seq).label("mx"))
            .where(Message.thread_id.in_(thread_ids))
            .group_by(Message.thread_id)
            .subquery()
        )
        rows = await self.s.scalars(
            select(Message).join(sub, (Message.thread_id == sub.c.thread_id) & (Message.seq == sub.c.mx))
        )
        return {m.thread_id: m for m in rows}

    async def add_search_tokens(self, message: Message, tokens: list[str]) -> None:
        for token in sorted(set(tokens)):
            self.s.add(MessageSearchToken(message_id=message.id, token=token, thread_id=message.thread_id))

    async def search(self, thread_id: str, token_groups: list[list[str]], after_seq: int, limit: int = 50) -> list[Message]:
        """token_groups: для каждого слова запроса — набор токенов-кандидатов (по всем эпохам ключа).

        Сообщение подходит, если для каждого слова совпал хотя бы один токен (логическое И).
        """
        if not token_groups:
            return []
        candidate_ids: set[str] | None = None
        for group in token_groups:
            rows = await self.s.scalars(
                select(MessageSearchToken.message_id).where(
                    MessageSearchToken.thread_id == thread_id, MessageSearchToken.token.in_(group)
                )
            )
            ids = set(rows)
            candidate_ids = ids if candidate_ids is None else candidate_ids & ids
            if not candidate_ids:
                return []
        rows = await self.s.scalars(
            select(Message).where(Message.id.in_(candidate_ids or set()), Message.seq > after_seq)
            .order_by(Message.seq.desc()).limit(limit)
        )
        return list(rows)

    async def delete_thread_messages(self, thread_id: str) -> None:
        await self.s.execute(delete(Message).where(Message.thread_id == thread_id))

    # --- вложения ---
    async def get_attachment(self, attachment_id: str) -> Attachment | None:
        return await self.s.get(Attachment, attachment_id)

    async def cached_media_bytes(self, owner_id: str) -> int:
        total = await self.s.scalar(
            select(func.coalesce(func.sum(Attachment.size_bytes), 0)).where(
                Attachment.owner_id == owner_id, Attachment.deleted_at.is_(None)
            )
        )
        return int(total or 0)
