"""Настройки аккаунта, список разрешённых для приглашения в группы и чёрный список."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    autolock: Mapped[str] = mapped_column(String(8), nullable=False, default="1m")          # 1m|5m|30m|1h|never
    group_invite_policy: Mapped[str] = mapped_column(String(10), nullable=False, default="everyone")  # everyone|nobody|allowlist
    markdown_preview: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    media_retention: Mapped[str] = mapped_column(String(6), nullable=False, default="30d")  # keep|30d|60d|6m
    show_online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    send_read_receipts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class GroupInviteAllow(Base):
    """Режим «Invite link only»: кому точечно разрешено добавлять пользователя в группы."""

    __tablename__ = "group_invite_allowlist"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    allowed_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class BlockedUser(Base):
    __tablename__ = "blocklist"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    blocked_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
