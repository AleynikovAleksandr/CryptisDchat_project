"""Сообщения (только шифротекст), слепой индекс для поиска и вложения."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_uuid, utcnow


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_messages_thread_seq"),
        UniqueConstraint("sender_id", "client_msg_id", name="uq_messages_sender_client_msg"),
        Index("ix_messages_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    client_msg_id: Mapped[str] = mapped_column(String(36), nullable=False)  # идемпотентность повторной отправки
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default="text")  # text | file | system
    # --- E2E-пакет (ТЗ 6.2): сервер хранит его как непрозрачные байты ---
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(24), nullable=False)
    signature: Mapped[bytes] = mapped_column(LargeBinary(96), nullable=False)
    key_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_key_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256(ciphertext)
    # --- связи ---
    reply_to_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    forwarded_from_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attachment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # исчезающие сообщения


class MessageSearchToken(Base):
    """Слепой индекс: HMAC(search_key, префикс слова), вычисленный клиентом.

    Сервер сравнивает токены, не зная ни слов, ни ключа.
    """

    __tablename__ = "message_search_tokens"
    __table_args__ = (Index("ix_message_search_tokens_thread_token", "thread_id", "token"),)

    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False)


class Attachment(Base):
    """Зашифрованный блоб (файл, миниатюра, аватар). Сервер не знает ни имени, ни содержимого."""

    __tablename__ = "attachments"
    __table_args__ = (Index("ix_attachments_owner_created", "owner_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(14), nullable=False, default="file")  # file|thumbnail|avatar|group_avatar
    thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    thumbnail_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False, default="application/octet-stream")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
