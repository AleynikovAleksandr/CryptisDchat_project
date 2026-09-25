"""Блокчейн-журнал: события доставки и батчи, записанные в TON (ТЗ 2, Gunicorn_Celery.md 4.2–4.3)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_uuid, utcnow


class ChainEvent(Base):
    """Факт отправки / доставки / прочтения. Хэш события — лист дерева Меркла батча."""

    __tablename__ = "chain_events"
    __table_args__ = (
        Index("ix_chain_events_message_id", "message_id"),
        Index("ix_chain_events_thread_type_seq", "thread_id", "event_type", "up_to_seq"),
        Index("ix_chain_events_batch_id", "batch_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # message.sent|message.delivered|message.read
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    up_to_seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="queued")  # queued|batched|confirmed
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    leaf_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    merkle_proof: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: [{"pos":"L|R","hash":hex}]
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class ChainBatch(Base):
    """Одна транзакция служебного кошелька с корнем дерева Меркла."""

    __tablename__ = "chain_batches"
    __table_args__ = (Index("ix_chain_batches_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    merkle_root: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    leaf_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")  # pending|submitted|confirmed|failed
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tx_lt: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
