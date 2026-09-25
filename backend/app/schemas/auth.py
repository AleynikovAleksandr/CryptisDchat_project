from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .users import MeOut


class ChallengeOut(BaseModel):
    payload: str
    domain: str
    expires_in: int
    dev_wallet_login: bool = False


class TonProofDomain(BaseModel):
    lengthBytes: int = Field(ge=1, le=256)  # noqa: N815 — имена полей как в TonConnect
    value: str = Field(max_length=256)


class TonProofBody(BaseModel):
    timestamp: int
    domain: TonProofDomain
    signature: str = Field(max_length=200)
    payload: str = Field(max_length=200)
    state_init: str | None = Field(default=None, max_length=8192)


class TonProofVerifyIn(BaseModel):
    address: str = Field(max_length=80)
    network: str = Field(default="-239", max_length=8)
    public_key: str = Field(min_length=64, max_length=64)
    proof: TonProofBody
    device_name: str = Field(default="Web browser", max_length=100)


class TokenPairOut(BaseModel):
    access_token: str
    access_expires_at: int
    refresh_token: str
    token_type: str = "bearer"
    is_new_user: bool = False
    user: MeOut


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=36, max_length=36)


class AccessOut(BaseModel):
    access_token: str
    access_expires_at: int
    refresh_token: str  # ротация: старый refresh-токен после обмена недействителен


class SessionOut(BaseModel):
    device_id: str
    name: str
    user_agent: str
    ip_address: str
    created_at: datetime
    last_seen_at: datetime
    current: bool


class WalletDisconnectIn(BaseModel):
    address: str = Field(max_length=80)
