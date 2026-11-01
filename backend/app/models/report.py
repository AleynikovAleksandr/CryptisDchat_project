"""Пользовательские жалобы, отправленные через приложение."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, new_uuid, utcnow


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (Index("ix_reports_status", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    reporter_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    resolution_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
