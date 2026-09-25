"""10.6 Сообщения и вложения.

POST /api/threads/{id}/messages принимает пакет в двух видах:
  * application/x-protobuf — бинарный MessagePacket (proto/cryptis.proto), основной формат клиента;
  * application/json — тот же пакет с base64-полями (удобно для отладки и тестов).
"""
from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, current_user, get_infra, get_session
from app.proto import wire
from app.schemas.messages import (
    ForwardIn,
    MessageOut,
    MessageProofOut,
    MessagesPageOut,
    SendMessageIn,
    UploadOut,
)
from app.services.errors import Invalid
from app.services.infra import Infra
from app.services.message_service import MessageService

router = APIRouter(prefix="/api", tags=["messages"])


def svc(session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)) -> MessageService:
    return MessageService(session, infra)


async def _read_packet(request: Request) -> SendMessageIn:
    ctype = request.headers.get("content-type", "")
    raw = await request.body()
    if len(raw) > 300_000:
        raise Invalid("packet too large")
    try:
        if ctype.startswith("application/x-protobuf"):
            p = wire.decode("MessagePacket", raw)
            return SendMessageIn(
                client_msg_id=p["client_msg_id"], key_epoch=p["key_epoch"],
                sender_key_version=p["sender_key_version"],
                nonce=base64.b64encode(p["nonce"]).decode(),
                ciphertext=base64.b64encode(p["ciphertext"]).decode(),
                signature=base64.b64encode(p["signature"]).decode(),
                kind=p["kind"] or "text",
                reply_to_id=p["reply_to_id"] or None,
                attachment_id=p["attachment_id"] or None,
                forwarded_from_id=p["forwarded_from_id"] or None,
                search_tokens=p["search_tokens"],
                push_preview=p["push_preview"] or None,
            )
        return SendMessageIn.model_validate_json(raw)
    except wire.ProtoError as exc:
        raise Invalid(f"malformed protobuf: {exc}") from exc
    except ValidationError as exc:
        raise Invalid(exc.errors(include_url=False)[0]["msg"]) from exc


@router.get("/threads/{thread_id}/messages", response_model=MessagesPageOut)
async def history(thread_id: str, before: int | None = None, limit: int = 50,
                  cu: CurrentUser = Depends(current_user), s: MessageService = Depends(svc)):
    return await s.page(cu.user, thread_id, before, limit)


@router.post("/threads/{thread_id}/messages", response_model=MessageOut, status_code=201)
async def send(thread_id: str, request: Request, cu: CurrentUser = Depends(current_user),
               s: MessageService = Depends(svc)):
    return await s.send(cu.user, thread_id, await _read_packet(request))


@router.get("/threads/{thread_id}/messages/search", response_model=list[MessageOut])
async def search(thread_id: str, q: list[str] = Query(default_factory=list),
                 cu: CurrentUser = Depends(current_user), s: MessageService = Depends(svc)):
    """q — по одному параметру на слово запроса; значение — токены слепого индекса через запятую
    (по токену на каждую известную клиенту эпоху ключа). Сервер не знает, какие слова ищут."""
    groups = [[t.strip() for t in item.split(",") if t.strip()] for item in q]
    return await s.search(cu.user, thread_id, groups)


@router.post("/messages/{message_id}/forward", response_model=MessageOut, status_code=201)
async def forward(message_id: str, body: ForwardIn, cu: CurrentUser = Depends(current_user),
                  s: MessageService = Depends(svc)):
    return await s.forward(cu.user, message_id, body.target_thread_id, body.packet)


@router.post("/uploads", response_model=UploadOut, status_code=201)
async def upload(file: UploadFile = File(...), kind: str = Form("file"), cu: CurrentUser = Depends(current_user),
                 s: MessageService = Depends(svc), infra: Infra = Depends(get_infra)):
    data = await file.read(infra.settings.max_upload_bytes + 1)
    return await s.upload(cu.user, data, kind, file.content_type or "")


@router.get("/messages/{message_id}/attachment")
async def attachment(message_id: str, cu: CurrentUser = Depends(current_user), s: MessageService = Depends(svc)):
    data, content_type = await s.attachment_for_message(cu.user, message_id)
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/messages/{message_id}/proof", response_model=MessageProofOut)
async def proof(message_id: str, cu: CurrentUser = Depends(current_user), s: MessageService = Depends(svc)):
    return await s.proof(cu.user, message_id)
