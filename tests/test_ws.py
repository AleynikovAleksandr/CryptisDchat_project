"""WebSocket: протокол кадров protobuf, Origin, аутентификация первым кадром, доставка событий."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.proto import wire
from tests import client_crypto as cc
from tests.conftest import login
from tests.test_api_messaging import open_dm


def auth_frame(token: str) -> bytes:
    return wire.encode("WsFrame", {"event": "auth", "json": json.dumps({"token": token})})


def recv(ws) -> dict:
    return wire.decode("WsFrame", ws.receive_bytes())


def recv_until(ws, event: str, limit: int = 10) -> dict:
    for _ in range(limit):
        f = recv(ws)
        if f["event"] == event:
            return f
    raise AssertionError(f"no {event}")


async def test_ws_rejects_foreign_origin_and_bad_token(app):
    with TestClient(app) as tc:
        with pytest.raises(WebSocketDisconnect):
            with tc.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_bytes()
        with tc.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
            ws.send_bytes(auth_frame("not-a-jwt"))
            assert recv(ws)["event"] == "error"


async def test_ws_delivers_message_typing_and_receipts(app, make_user, client):
    alice, bob = await make_user("Alice"), await make_user("Bob")
    thread_id, conv_key = await open_dm(alice, bob)
    packet = cc.make_packet(alice.keys, thread_id, conv_key, 1, {"text": "realtime"})
    bob_other = await login(client, bob.wallet)

    with TestClient(app) as tc:
        with tc.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws_bob, \
             tc.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws_alice:
            ws_bob.send_bytes(auth_frame(bob.access))
            assert recv(ws_bob)["event"] == "ready"
            ws_alice.send_bytes(auth_frame(alice.access))
            assert recv(ws_alice)["event"] == "ready"

            r = tc.post(f"/api/threads/{thread_id}/messages", json=packet,
                        headers={"Authorization": f"Bearer {alice.access}"})
            assert r.status_code == 201
            frame = recv_until(ws_bob, "message.new")
            assert frame["seq"] == 1 and frame["sender_id"] == alice.id
            assert cc.decrypt_message(conv_key, {
                "ciphertext": base64.b64encode(frame["packet"]["ciphertext"]).decode(),
                "nonce": base64.b64encode(frame["packet"]["nonce"]).decode(),
            })["text"] == "realtime"

            # Bob подтверждает доставку по WS → Alice получает message.delivered
            ws_bob.send_bytes(wire.encode("WsFrame", {"event": "ack.delivered", "thread_id": thread_id, "seq": 1}))
            delivered = recv_until(ws_alice, "message.delivered")
            assert delivered["seq"] == 1 and delivered["sender_id"] == bob.id

            ws_bob.send_bytes(wire.encode("WsFrame", {"event": "typing.start", "thread_id": thread_id}))
            typing = recv_until(ws_alice, "typing.start")
            assert typing["thread_id"] == thread_id

            ws_bob.send_bytes(wire.encode("WsFrame", {"event": "heartbeat"}))
            assert recv_until(ws_bob, "heartbeat")["event"] == "heartbeat"

            # Logging out other devices closes Bob's old socket and retains the caller.
            headers = {"Authorization": f"Bearer {bob_other['access_token']}"}
            assert tc.post("/api/auth/logout-all", headers=headers).status_code == 204
            assert recv_until(ws_bob, "session.revoked")["event"] == "session.revoked"
            assert tc.get("/api/me", headers=headers).status_code == 200
    await asyncio.sleep(0)
