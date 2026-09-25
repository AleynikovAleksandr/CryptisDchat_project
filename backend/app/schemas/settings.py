from __future__ import annotations

from pydantic import BaseModel, Field


class SettingsOut(BaseModel):
    autolock: str
    group_invite_policy: str
    markdown_preview: bool
    media_retention: str
    show_online: bool
    send_read_receipts: bool
    cached_media_bytes: int


class SettingsPatchIn(BaseModel):
    autolock: str | None = Field(default=None, pattern="^(1m|5m|30m|1h|never)$")
    group_invite_policy: str | None = Field(default=None, pattern="^(everyone|nobody|allowlist)$")
    markdown_preview: bool | None = None
    media_retention: str | None = Field(default=None, pattern="^(keep|30d|60d|6m)$")
    show_online: bool | None = None
    send_read_receipts: bool | None = None
