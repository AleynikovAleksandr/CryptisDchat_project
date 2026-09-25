from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KeySetIn(BaseModel):
    identity_pub: str = Field(max_length=400)
    signing_pub: str = Field(max_length=400)


class KeySetOut(BaseModel):
    user_id: str
    version: int
    identity_pub: str
    signing_pub: str
    created_at: datetime


class WrappedKeyIn(BaseModel):
    recipient_id: str
    recipient_key_version: int
    wrapped_key: str = Field(max_length=2048)


class ConversationKeysIn(BaseModel):
    epoch: int = Field(ge=1)
    keys: list[WrappedKeyIn] = Field(min_length=1, max_length=2)


class ConversationKeyOut(BaseModel):
    epoch: int
    wrapped_key: str
    recipient_key_version: int
    created_by: str
    created_at: datetime


# --- дерево ключей группы (ТЗ 6.8) ---
class TreePlanOut(BaseModel):
    thread_id: str
    next_epoch: int
    capacity: int
    root: int
    path: list[int]
    blanks: list[int]
    targets: dict[int, list[int]]
    known_pubs: dict[int, str]
    reason: str
    membership_event_ids: list[str]


class CommitCiphertext(BaseModel):
    target: int
    ct: str = Field(max_length=4096)


class CommitNode(BaseModel):
    index: int
    public_key: str = Field(max_length=400)
    ciphertexts: list[CommitCiphertext]


class TreeCommitIn(BaseModel):
    epoch: int = Field(ge=1)
    nodes: list[CommitNode] = Field(max_length=512)
    root_ciphertext: str = Field(max_length=4096)


class TreeCommitOut(BaseModel):
    epoch: int
    committer_id: str
    reason: str
    nodes: list[CommitNode]
    blanks: list[int]
    root: int
    root_ciphertext: str
    my_leaf: int | None
    created_at: datetime


# --- резервное копирование (ТЗ 6.6) ---
class SealedShareIn(BaseModel):
    realm_index: int = Field(ge=0, le=15)
    sealed: str = Field(max_length=4096)


class BackupIn(BaseModel):
    ciphertext: str = Field(max_length=200_000)
    kdf_salt: str = Field(max_length=64)
    kdf_iterations: int = Field(ge=100_000, le=5_000_000)
    threshold: int = Field(ge=2, le=15)
    shares: list[SealedShareIn] = Field(min_length=2, max_length=15)


class RealmOut(BaseModel):
    index: int
    public_key: str


class BackupMetaOut(BaseModel):
    exists: bool
    version: int | None = None
    kdf_salt: str | None = None
    kdf_iterations: int | None = None
    threshold: int | None = None
    share_count: int | None = None
    status: str | None = None
    ciphertext: str | None = None
    realms: list[RealmOut]


class RecoveryRequestItem(BaseModel):
    realm_index: int = Field(ge=0, le=15)
    sealed_request: str = Field(max_length=4096)


class RecoveryIn(BaseModel):
    requests: list[RecoveryRequestItem] = Field(min_length=2, max_length=15)


class RecoveryOut(BaseModel):
    id: str
    status: str
    attempts_left: int | None
    shares: list[dict] | None
