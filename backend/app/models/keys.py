"""Криптографический материал на стороне сервера (ТЗ 6).

Сервер хранит ТОЛЬКО публичные ключи и зашифрованные («обёрнутые») блобы.
Приватные ключи и открытые Conversation key сюда никогда не попадают.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_uuid, utcnow


class UserKeySet(Base):
    """Публичные Identity (ECDH P-256) и Signing (ECDSA P-256) ключи пользователя.

    Версии сохраняются, чтобы подписи старых сообщений оставались проверяемыми
    после сброса ключей.
    """

    __tablename__ = "user_key_sets"
    __table_args__ = (UniqueConstraint("user_id", "version", name="uq_user_key_sets_user_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_pub: Mapped[str] = mapped_column(Text, nullable=False)  # SPKI, base64
    signing_pub: Mapped[str] = mapped_column(Text, nullable=False)   # SPKI, base64
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class ConversationKey(Base):
    """Conversation key личного чата, обёрнутый ECIES для конкретного получателя (ТЗ 6.3)."""

    __tablename__ = "conversation_keys"
    __table_args__ = (
        UniqueConstraint("thread_id", "epoch", "recipient_id", name="uq_conversation_keys_thread_epoch_recipient"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    recipient_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    wrapped_key: Mapped[str] = mapped_column(Text, nullable=False)   # ECIES-блоб, base64
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class KeyBackup(Base):
    """Резервная копия ключей устройства (ТЗ 6.6).

    `ciphertext` — пакет приватных ключей, зашифрованный случайным ключом K
    (AES-256-GCM на клиенте). Сам K разделён по Шамиру 2-из-3 и лежит по долям
    на независимых realm-сервисах, сюда он не попадает.
    """

    __tablename__ = "key_backups"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    kdf_salt: Mapped[str] = mapped_column(String(64), nullable=False)          # base64, соль PBKDF2 для PIN
    kdf_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    share_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")  # pending|active|destroyed
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class KeyBackupShare(Base):
    """Статус доли секрета на конкретном realm (сама доля хранится только на realm)."""

    __tablename__ = "key_backup_shares"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    realm_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    backup_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")  # pending|stored|failed|destroyed
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class KeyRecoveryRequest(Base):
    """Запрос восстановления ключей на новом устройстве, исполняется Celery (очередь keys)."""

    __tablename__ = "key_recovery_requests"
    __table_args__ = (Index("ix_key_recovery_requests_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")  # pending|done|wrong_pin|destroyed|failed
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # доли, зашифрованные под эфемерный ключ клиента
    attempts_left: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
