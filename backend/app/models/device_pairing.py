"""Persistent rate-limit state for QR pairing generation."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class DevicePairingQuota(Base):
    __tablename__ = "device_pairing_quotas"
    __table_args__ = (Index("ix_device_pairing_quotas_updated_at", "updated_at"),)

    identity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[list[object]] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
