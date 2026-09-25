from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MessageOut(BaseModel):
    """Сообщение в том виде, в каком его знает сервер: шифротекст + метаданные."""

    id: str
    thread_id: str
    seq: int
    sender_id: str
    client_msg_id: str
    kind: str
    ciphertext: str      # base64
    nonce: str           # base64
    signature: str       # base64
    key_epoch: int
    sender_key_version: int
    reply_to_id: str | None
    forwarded_from_id: str | None
    attachment_id: str | None
    created_at: datetime
    expires_at: datetime | None
    confirmed: bool = False  # событие отправки подтверждено транзакцией в TON


class MessagesPageOut(BaseModel):
    items: list[MessageOut]
    next_before: int | None


class SendMessageIn(BaseModel):
    """JSON-вариант MessagePacket (proto/cryptis.proto); бинарные поля — base64."""

    client_msg_id: str = Field(min_length=36, max_length=36)
    key_epoch: int = Field(ge=1)
    sender_key_version: int = Field(ge=1)
    nonce: str = Field(max_length=64)
    ciphertext: str = Field(max_length=200_000)
    signature: str = Field(max_length=200)
    kind: str = Field(default="text", pattern="^(text|file)$")
    reply_to_id: str | None = None
    attachment_id: str | None = None
    forwarded_from_id: str | None = None
    search_tokens: list[str] = Field(default_factory=list, max_length=256)
    push_preview: str | None = Field(default=None, max_length=120)


class ForwardIn(BaseModel):
    """Пересылка: клиент заново шифрует содержимое ключом целевого чата."""

    target_thread_id: str
    packet: SendMessageIn


class UploadOut(BaseModel):
    id: str
    size: int
    kind: str


class ChainBatchOut(BaseModel):
    id: str
    merkle_root: str
    status: str
    tx_hash: str | None
    confirmed_at: datetime | None


class ChainProofOut(BaseModel):
    event_type: str
    actor_id: str
    up_to_seq: int | None
    event_hash: str
    status: str
    merkle_proof: list[dict] | None
    batch: ChainBatchOut | None
    created_at: datetime


class MessageProofOut(BaseModel):
    message_id: str
    content_hash: str
    events: list[ChainProofOut]
