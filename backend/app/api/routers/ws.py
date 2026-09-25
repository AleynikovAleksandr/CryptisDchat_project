"""10.11 WebSocket — события реального времени (ТЗ 6.4).

Кадры — бинарный protobuf `WsFrame` (proto/cryptis.proto).

Защита (ТЗ 7):
  * проверка заголовка Origin — от подмены происхождения запроса (CSWSH);
  * токен передаётся первым кадром `auth`, а не в URL (не попадает в access-лог);
  * лимит частоты входящих кадров; истёкший JWT → кадр error + закрытие 4003.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.proto import wire
from app.repositories import SettingsRepository, ThreadRepository
from app.repositories.database import get_sessionmaker
from app.services import cache, security
from app.services.auth_service import AuthService
from app.services.errors import DomainError
from app.services.infra import Infra
from app.services.thread_service import ThreadService

router = APIRouter()

AUTH_TIMEOUT = 10
MAX_FRAMES_PER_10S = 60
CLOSE_POLICY = 1008
CLOSE_REVOKED = 4001
CLOSE_EXPIRED = 4003


def frame(event: str, **fields) -> bytes:
    return wire.encode("WsFrame", {"event": event, "ts": int(time.time() * 1000), **fields})


def event_to_frame(event: str, data: dict) -> bytes:
    if event == "message.new":
        packet = {
            "client_msg_id": data["client_msg_id"], "thread_id": data["thread_id"],
            "key_epoch": data["key_epoch"], "sender_key_version": data["sender_key_version"],
            "nonce": base64.b64decode(data["nonce"]), "ciphertext": base64.b64decode(data["ciphertext"]),
            "signature": base64.b64decode(data["signature"]), "kind": data["kind"],
            "reply_to_id": data.get("reply_to_id") or "", "attachment_id": data.get("attachment_id") or "",
            "forwarded_from_id": data.get("forwarded_from_id") or "",
        }
        meta = {k: data.get(k) for k in ("created_at", "expires_at", "id")}
        return frame(event, thread_id=data["thread_id"], message_id=data["id"], seq=data["seq"],
                     sender_id=data["sender_id"], packet=packet, json=json.dumps(meta, default=str))
    return frame(event, thread_id=data.get("thread_id", ""), sender_id=data.get("user_id", ""),
                 seq=int(data.get("up_to_seq") or 0), json=json.dumps(data, default=str))


class Connection:
    def __init__(self, ws: WebSocket, infra: Infra) -> None:
        self.ws = ws
        self.infra = infra
        self.user_id = ""
        self.device_id = ""
        self.token_exp = 0
        self.show_online = True
        self.member_of: dict[str, bool] = {}
        self.frame_times: list[float] = []

    async def authenticate(self) -> bool:
        try:
            raw = await asyncio.wait_for(self.ws.receive_bytes(), timeout=AUTH_TIMEOUT)
            f = wire.decode("WsFrame", raw)
            token = json.loads(f["json"] or "{}").get("token", "")
        except (asyncio.TimeoutError, wire.ProtoError, ValueError, KeyError):
            await self.ws.close(code=CLOSE_POLICY)
            return False
        if f["event"] != "auth":
            await self.ws.close(code=CLOSE_POLICY)
            return False
        async with get_sessionmaker()() as session:
            try:
                user, device_id = await AuthService(session, self.infra).authenticate(token)
            except DomainError:
                await self.ws.send_bytes(frame("error", json=json.dumps({"code": "unauthorized"})))
                await self.ws.close(code=CLOSE_POLICY)
                return False
            prefs = await SettingsRepository(session).get(user.id)
            self.show_online = prefs.show_online
            await session.commit()
        self.user_id, self.device_id = user.id, device_id
        self.token_exp = security.decode_access_token(token).expires_at
        return True

    def rate_ok(self) -> bool:
        now = time.monotonic()
        self.frame_times = [t for t in self.frame_times if now - t < 10]
        self.frame_times.append(now)
        return len(self.frame_times) <= MAX_FRAMES_PER_10S

    async def is_member(self, thread_id: str) -> bool:
        if thread_id not in self.member_of:
            async with get_sessionmaker()() as session:
                m = await ThreadRepository(session).membership(thread_id, self.user_id)
                self.member_of[thread_id] = m is not None and m.left_at is None
        return self.member_of[thread_id]

    async def handle_client_frame(self, raw: bytes) -> None:
        f = wire.decode("WsFrame", raw)
        event = f["event"]
        if event == "auth":  # продление: клиент присылает свежий JWT до истечения старого
            token = json.loads(f["json"] or "{}").get("token", "")
            claims = security.decode_access_token(token)
            if claims.user_id == self.user_id:
                self.token_exp = claims.expires_at
            return
        if event == "heartbeat":
            if self.show_online:
                await cache.touch_presence(self.infra.redis, self.user_id)
            await self.ws.send_bytes(frame("heartbeat"))
            return
        thread_id = f["thread_id"]
        if not thread_id or not await self.is_member(thread_id):
            return
        if event in ("typing.start", "typing.stop"):
            async with get_sessionmaker()() as session:
                member_ids = await ThreadRepository(session).active_member_ids(thread_id)
            for uid in member_ids:
                if uid != self.user_id:
                    await self.infra.bus.publish(uid, event, {"thread_id": thread_id, "user_id": self.user_id})
        elif event == "ack.delivered":
            async with get_sessionmaker()() as session:
                await ThreadService(session, self.infra).mark_delivered(self.user_id, thread_id, int(f["seq"]))

    async def pump_events(self) -> None:
        async for event, data in self.infra.bus.subscribe(self.user_id):
            if event == "session.revoked" and data.get("device_id") in ("*", self.device_id):
                await self.ws.send_bytes(event_to_frame(event, data))
                await self.ws.close(code=CLOSE_REVOKED)
                return
            if event == "thread.updated" and data.get("removed_user_id") == self.user_id:
                self.member_of.pop(data.get("thread_id", ""), None)
            await self.ws.send_bytes(event_to_frame(event, data))


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    infra: Infra = ws.app.state.infra
    origin = ws.headers.get("origin")
    if origin is not None and origin not in infra.settings.ws_origin_list:
        await ws.close(code=CLOSE_POLICY)
        return
    await ws.accept()
    conn = Connection(ws, infra)
    if not await conn.authenticate():
        return
    if conn.show_online:
        await cache.touch_presence(infra.redis, conn.user_id)
    await ws.send_bytes(frame("ready", sender_id=conn.user_id))

    pump = asyncio.create_task(conn.pump_events())
    try:
        while True:
            receive = asyncio.create_task(ws.receive_bytes())
            done, _ = await asyncio.wait({receive, pump}, return_when=asyncio.FIRST_COMPLETED)
            if pump in done:
                receive.cancel()
                break
            raw = receive.result()
            if time.time() > conn.token_exp:
                await ws.send_bytes(frame("error", json=json.dumps({"code": "token_expired"})))
                await ws.close(code=CLOSE_EXPIRED)
                break
            if not conn.rate_ok():
                await ws.send_bytes(frame("error", json=json.dumps({"code": "rate_limited"})))
                continue
            try:
                await conn.handle_client_frame(raw)
            except (wire.ProtoError, ValueError, KeyError, security.TokenError):
                await ws.send_bytes(frame("error", json=json.dumps({"code": "bad_frame"})))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
