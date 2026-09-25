from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .messages import MessageOut
from .users import UserPublicOut


class MemberOut(BaseModel):
    user: UserPublicOut
    role: str
    joined_at: datetime


class ThreadOut(BaseModel):
    id: str
    kind: str
    title: str | None
    avatar_url: str | None
    avatar_tint: str
    peer: UserPublicOut | None
    members_count: int
    my_role: str
    muted: bool
    unread: int
    last_message: MessageOut | None
    last_message_at: datetime | None
    created_at: datetime
    disappearing_seconds: int | None
    screenshot_block: bool
    key_epoch: int
    rotation_pending: bool
    last_read_seq: int
    peer_last_read_seq: int
    peer_last_delivered_seq: int


class ThreadDetailOut(ThreadOut):
    members: list[MemberOut]


class DirectThreadIn(BaseModel):
    user_id: str = Field(min_length=36, max_length=36)


class MuteIn(BaseModel):
    muted: bool


class ReadIn(BaseModel):
    up_to_seq: int | None = Field(default=None, ge=0)


class DisappearingIn(BaseModel):
    seconds: int | None = Field(default=None, ge=0, le=60 * 60 * 24 * 28)


class ScreenshotBlockIn(BaseModel):
    enabled: bool


class GroupCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    member_ids: list[str] = Field(min_length=1, max_length=200)
    avatar_upload_id: str | None = None


class GroupPatchIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    avatar_upload_id: str | None = None


class AddMembersIn(BaseModel):
    user_ids: list[str] = Field(min_length=1, max_length=100)
