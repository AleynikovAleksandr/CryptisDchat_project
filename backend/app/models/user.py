"""Пользователи, кошельки, устройства и сессии (ТЗ 4, 4.2)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_uuid, utcnow


class User(TimestampMixin, Base):
    """Пользователь. UUID — первичный ключ и `sub` в JWT; TON-адрес живёт в user_wallets."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    display_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    username: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    bio: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    avatar_upload_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    avatar_tint: Mapped[str] = mapped_column(String(32), nullable=False, default="hsl(204 60% 45%)")
    is_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # «Выйти со всех устройств»: JWT, выданные раньше этой отметки, отклоняются
    sessions_revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    wallets: Mapped[list[UserWallet]] = relationship(back_populates="user", lazy="selectin")


class UserWallet(Base):
    """Привязанный TON-кошелёк. Активный адрес уникален во всей системе."""

    __tablename__ = "user_wallets"
    __table_args__ = (Index("ix_user_wallets_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    address: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)  # raw-форма "0:<hex>"
    friendly_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)          # ed25519 hex
    network: Mapped[str] = mapped_column(String(8), nullable=False, default="-239")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    user: Mapped[User] = relationship(back_populates="wallets")


class Device(Base):
    """Устройство (браузер) пользователя. К нему привязан refresh-токен.

    Сервер выдаёт браузеру ключ устройства в HttpOnly-cookie (новый при каждом входе);
    повторный вход с ним переиспользует запись, а не создаёт новое устройство.
    В БД лежит только SHA-256 ключа.
    """

    __tablename__ = "devices"
    __table_args__ = (Index("ix_devices_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="Web browser")
    user_agent: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    client_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # при повторном входе на отозванное устройство revoked_at сбрасывается, а отметка
    # переезжает сюда: JWT, выданные до отзыва, так и остаются недействительными
    tokens_revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RefreshToken(Base):
    """Refresh-токен (UUID). В БД хранится только SHA-256 от него, не сам токен."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_device_id", "device_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    replaced_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PushToken(Base):
    """Токен push-уведомлений устройства (APNs / FCM / WebPush)."""

    __tablename__ = "push_tokens"
    __table_args__ = (UniqueConstraint("device_id", "platform", name="uq_push_tokens_device_platform"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id: Mapped[str] = mapped_column(String(36), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(10), nullable=False)  # fcm | apns | webpush
    token: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
