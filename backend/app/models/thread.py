"""Чаты (личные и групповые), участники, журнал состава и дерево ключей группы."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_uuid, utcnow


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (Index("ix_threads_last_message_at", "last_message_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # direct | group
    title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    avatar_upload_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    avatar_tint: Mapped[str] = mapped_column(String(32), nullable=False, default="hsl(265 40% 48%)")
    # "uuidA:uuidB" (отсортировано) — гарантирует один личный чат на пару пользователей
    direct_key: Mapped[str | None] = mapped_column(String(73), nullable=True, unique=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # настройки уровня чата (Interface_and_API.md, 10.8)
    disappearing_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    screenshot_block: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # ключи: у личного чата — номер эпохи Conversation key, у группы — эпоха TreeKEM
    key_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tree_leaf_capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rotation_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ThreadMember(Base):
    __tablename__ = "thread_members"
    __table_args__ = (Index("ix_thread_members_user_id", "user_id"),)

    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(8), nullable=False, default="member")  # owner | admin | member
    joined_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # водяные знаки доставки/прочтения: всё с seq <= значения доставлено/прочитано
    last_delivered_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_read_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # «Clear conversation»: сообщения с seq <= cleared_seq скрыты для этого участника
    cleared_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # «Delete conversation»: чат скрыт из списка, пока не придёт новое сообщение
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    joined_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    leaf_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # позиция листа в дереве ключей группы


class GroupMembershipEvent(Base):
    """Изменение состава группы. Каждое фиксируется отдельной транзакцией в TON (ТЗ 2, 6.8)."""

    __tablename__ = "group_membership_events"
    __table_args__ = (Index("ix_group_membership_events_thread_id", "thread_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # create|add|remove|leave|promote|demote
    subject_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    leaf_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    chain_status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")  # pending|submitted|confirmed|failed
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rotation_status: Mapped[str] = mapped_column(String(10), nullable=False, default="waiting")  # waiting|requested|done
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GroupTreeNode(Base):
    """Узел TreeKEM-подобного дерева ключей группы: только публичный ключ (или пусто)."""

    __tablename__ = "group_tree_nodes"

    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id", ondelete="CASCADE"), primary_key=True)
    node_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # SPKI base64; NULL = пустой узел
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # только для листьев
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class GroupKeyCommit(Base):
    """Коммит обновления ветки дерева: новые публичные ключи узлов и их приватные ключи,
    зашифрованные ECIES под публичные ключи соседних поддеревьев."""

    __tablename__ = "group_key_commits"
    __table_args__ = (UniqueConstraint("thread_id", "epoch", name="uq_group_key_commits_thread_epoch"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    committer_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(16), nullable=False)  # create|add|remove|leave|periodic
    membership_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
