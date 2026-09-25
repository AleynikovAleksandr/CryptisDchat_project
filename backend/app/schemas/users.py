from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.]{3,15}$")


class WalletOut(BaseModel):
    address: str
    friendly_address: str
    network: str
    is_primary: bool
    linked_at: datetime


class MeOut(BaseModel):
    id: str
    display_name: str
    username: str | None
    bio: str
    avatar_url: str | None
    avatar_tint: str
    ton_address: str | None
    ton_address_friendly: str | None
    wallet_connected: bool
    wallets: list[WalletOut]
    key_version: int | None
    has_backup: bool
    backup_status: str | None
    created_at: datetime


class MePatchIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=50)
    username: str | None = Field(default=None, max_length=16)
    bio: str | None = Field(default=None, max_length=160)

    @field_validator("username")
    @classmethod
    def _username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.lstrip("@")
        if v == "":
            return v
        if not USERNAME_RE.match(v):
            raise ValueError("username: 3-15 символов, латиница, цифры, _ и .")
        return v.lower()


class UserPublicOut(BaseModel):
    id: str
    display_name: str
    username: str | None
    avatar_url: str | None
    avatar_tint: str
    ton_address_friendly: str | None
    online: bool = False


class PushTokenIn(BaseModel):
    platform: str = Field(pattern="^(fcm|apns|webpush)$")
    token: str = Field(min_length=8, max_length=512)


class ReportIn(BaseModel):
    reason: str = Field(pattern="^(spam|abuse|illegal|other)$")
    details: str = Field(default="", max_length=2000)
    target_user_id: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
