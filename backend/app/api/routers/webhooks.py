"""Подписанные обратные вызовы блокчейн-провайдера."""
from __future__ import annotations

import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_infra, get_session
from app.models import ChainBatch, GroupMembershipEvent
from app.services.infra import Infra

router = APIRouter(tags=["webhooks"])


async def _signed_payload(request: Request, infra: Infra) -> dict:
    body = await request.body()
    expected = hmac.new(infra.settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    given = request.headers.get("X-Cryptis-Signature", "")
    if not hmac.compare_digest(expected, given):
        raise HTTPException(status_code=401)
    try:
        payload = json.loads(body or b"{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400)
    return payload


@router.post("/webhooks/ton-tx-status")
async def ton_tx_status(request: Request, session: AsyncSession = Depends(get_session),
                        infra: Infra = Depends(get_infra)) -> dict | JSONResponse:
    payload = await _signed_payload(request, infra)
    tx_ref = str(payload.get("tx_ref") or payload.get("comment") or "")
    if not tx_ref:
        raise HTTPException(status_code=400)
    batch = await session.scalar(select(ChainBatch).where(ChainBatch.tx_hash == tx_ref))
    if batch is not None:
        infra.tasks.enqueue("blockchain.confirm_batch", batch.id)
        return {"status": "queued", "batch_id": batch.id}
    event = await session.scalar(select(GroupMembershipEvent).where(GroupMembershipEvent.tx_hash == tx_ref))
    if event is not None:
        infra.tasks.enqueue("blockchain.confirm_membership", event.id)
        return {"status": "queued", "membership_event_id": event.id}
    return JSONResponse({"status": "unknown"}, status_code=404)


@router.post("/webhooks/tonconnect")
async def tonconnect(request: Request, infra: Infra = Depends(get_infra)) -> dict:
    await _signed_payload(request, infra)
    return {"status": "ok"}
