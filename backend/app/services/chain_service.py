"""Фиксация фактов отправки/доставки/прочтения для блокчейн-журнала (ТЗ 2).

API не общается с блокчейном напрямую (ТЗ 7 — наименьшие привилегии): он только
сохраняет событие в БД и кладёт его id в очередь ожидания Redis `chain:pending`.
Батчинг в дерево Меркла и отправку транзакции делает очередь Celery `blockchain`.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChainEvent, Message, new_uuid, utcnow
from app.repositories import ChainRepository
from app.schemas.messages import ChainBatchOut, ChainProofOut, MessageProofOut
from app.services import cache, event_hash
from app.services.infra import Infra

FLUSH_TASK = "blockchain.flush_batch"


class ChainService:
    def __init__(self, session: AsyncSession, infra: Infra) -> None:
        self.s = session
        self.infra = infra
        self.repo = ChainRepository(session)
        self._pending: list[str] = []

    def record_sent(self, message: Message) -> ChainEvent:
        ev = ChainEvent(
            id=new_uuid(),  # id нужен до flush: он уходит в очередь Redis после коммита
            event_type="message.sent",
            thread_id=message.thread_id,
            message_id=message.id,
            actor_id=message.sender_id,
            up_to_seq=message.seq,
            event_hash=event_hash.message_sent_hash(
                message.id, message.thread_id, message.sender_id, message.seq, message.content_hash, message.created_at
            ),
        )
        self.repo.add_event(ev)
        self._pending.append(ev.id)
        return ev

    def record_receipt(self, event_type: str, thread_id: str, actor_id: str, up_to_seq: int,
                       ts: datetime | None = None) -> ChainEvent:
        ts = ts or utcnow()
        ev = ChainEvent(
            id=new_uuid(),
            event_type=event_type,
            thread_id=thread_id,
            actor_id=actor_id,
            up_to_seq=up_to_seq,
            event_hash=event_hash.receipt_hash(event_type, thread_id, actor_id, up_to_seq, ts),
            created_at=ts,
        )
        self.repo.add_event(ev)
        self._pending.append(ev.id)
        return ev

    async def after_commit(self) -> None:
        """Вызывается ПОСЛЕ коммита транзакции: публикует id событий в очередь ожидания."""
        if not self._pending:
            return
        length = 0
        for event_id in self._pending:
            length = await cache.push_chain_event(self.infra.redis, event_id)
        self._pending.clear()
        # «раз в N минут ИЛИ как только накопилось N записей» (Gunicorn_Celery.md 4.2)
        if length >= self.infra.settings.chain_batch_max_items:
            self.infra.tasks.enqueue(FLUSH_TASK)

    async def proof_for(self, message: Message) -> MessageProofOut:
        events = await self.repo.events_for_message(message.id, message.thread_id, message.seq)
        batches = await self.repo.batches({e.batch_id for e in events if e.batch_id})
        out: list[ChainProofOut] = []
        for e in events:
            b = batches.get(e.batch_id) if e.batch_id else None
            out.append(ChainProofOut(
                event_type=e.event_type,
                actor_id=e.actor_id,
                up_to_seq=e.up_to_seq,
                event_hash=e.event_hash,
                status=e.status,
                merkle_proof=json.loads(e.merkle_proof) if e.merkle_proof else None,
                batch=ChainBatchOut(id=b.id, merkle_root=b.merkle_root, status=b.status,
                                    tx_hash=b.tx_hash, confirmed_at=b.confirmed_at) if b else None,
                created_at=e.created_at,
            ))
        return MessageProofOut(message_id=message.id, content_hash=message.content_hash, events=out)
