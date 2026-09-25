from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChainBatch, ChainEvent


class ChainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    def add_event(self, event: ChainEvent) -> None:
        self.s.add(event)

    async def events_for_message(self, message_id: str, thread_id: str, seq: int) -> list[ChainEvent]:
        """Событие отправки сообщения + первые квитанции доставки/прочтения, покрывающие его seq
        (по одной на каждого участника)."""
        sent = await self.s.scalars(
            select(ChainEvent).where(ChainEvent.message_id == message_id, ChainEvent.event_type == "message.sent")
        )
        receipts = await self.s.scalars(
            select(ChainEvent).where(
                ChainEvent.thread_id == thread_id,
                or_(ChainEvent.event_type == "message.delivered", ChainEvent.event_type == "message.read"),
                ChainEvent.up_to_seq >= seq,
            ).order_by(ChainEvent.up_to_seq, ChainEvent.created_at)
        )
        first: dict[tuple[str, str], ChainEvent] = {}
        for ev in receipts:
            first.setdefault((ev.event_type, ev.actor_id), ev)
        return list(sent) + list(first.values())

    async def batches(self, ids: set[str]) -> dict[str, ChainBatch]:
        if not ids:
            return {}
        rows = await self.s.scalars(select(ChainBatch).where(ChainBatch.id.in_(ids)))
        return {b.id: b for b in rows}

    async def confirmed_message_ids(self, message_ids: list[str]) -> set[str]:
        if not message_ids:
            return set()
        rows = await self.s.scalars(
            select(ChainEvent.message_id).where(
                ChainEvent.message_id.in_(message_ids), ChainEvent.event_type == "message.sent",
                ChainEvent.status == "confirmed",
            )
        )
        return {r for r in rows if r}
