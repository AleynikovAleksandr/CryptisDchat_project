"""ТЗ 4.2: ton_proof → UUID-пользователь → JWT + Refresh, авто-вход, отзыв сессий."""
from __future__ import annotations

from tests import client_crypto as cc
from tests.conftest import login


async def test_first_login_creates_user_second_login_reuses_it(client):
    wallet = cc.DevWallet()
    first = await login(client, wallet)
    assert first["is_new_user"] is True
    assert len(first["user"]["id"]) == 36 and first["user"]["ton_address"] == wallet.address
    second = await login(client, wallet)
    assert second["is_new_user"] is False and second["user"]["id"] == first["user"]["id"]


async def test_payload_is_single_use_and_signature_is_checked(client):
    wallet = cc.DevWallet()
    ch = (await client.post("/api/auth/ton-proof/challenge")).json()
    body = wallet.proof(ch["payload"], ch["domain"])
    assert (await client.post("/api/auth/ton-proof/verify", json=body)).status_code == 200
    replay = await client.post("/api/auth/ton-proof/verify", json=body)
    assert replay.status_code == 401 and replay.json()["error"] == "proof_invalid"

    ch = (await client.post("/api/auth/ton-proof/challenge")).json()
    forged = cc.DevWallet().proof(ch["payload"], ch["domain"])
    forged["address"] = wallet.address  # чужой адрес с собственной подписью
    r = await client.post("/api/auth/ton-proof/verify", json=forged)
    assert r.status_code == 401


async def test_refresh_rotates_and_detects_reuse(client):
    data = await login(client)
    old_refresh = data["refresh_token"]
    r = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200
    new = r.json()
    me = await client.get("/api/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert me.status_code == 200

    # повторное предъявление уже обменянного токена = кража → сессия устройства отозвана
    reuse = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401
    r = await client.post("/api/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert r.status_code == 401
    me = await client.get("/api/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert me.status_code == 401 and me.json()["error"] == "token_revoked"


async def test_logout_all_revokes_every_device(client):
    wallet = cc.DevWallet()
    a = await login(client, wallet)
    b = await login(client, wallet)
    ha = {"Authorization": f"Bearer {a['access_token']}"}
    sessions = (await client.get("/api/auth/sessions", headers=ha)).json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1

    assert (await client.post("/api/auth/logout-all", headers=ha)).status_code == 204
    for token in (a["access_token"], b["access_token"]):
        r = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
    r = await client.post("/api/auth/refresh", json={"refresh_token": b["refresh_token"]})
    assert r.status_code == 401


async def test_logout_single_device_keeps_others(client):
    wallet = cc.DevWallet()
    a = await login(client, wallet)
    b = await login(client, wallet)
    assert (await client.post("/api/auth/logout", headers={"Authorization": f"Bearer {a['access_token']}"})).status_code == 204
    assert (await client.get("/api/me", headers={"Authorization": f"Bearer {a['access_token']}"})).status_code == 401
    assert (await client.get("/api/me", headers={"Authorization": f"Bearer {b['access_token']}"})).status_code == 200


async def test_profile_and_wallet_rules(make_user, client):
    u = await make_user("Alice")
    r = await u.patch("/api/me", {"username": "@Alice_1", "bio": "hi"})
    assert r.status_code == 200 and r.json()["username"] == "alice_1"
    other = await make_user("Bob")
    taken = await other.patch("/api/me", {"username": "alice_1"})
    assert taken.status_code == 409

    # единственный кошелёк отвязать нельзя: он и есть логин
    r = await u.post("/api/wallet/disconnect", {"address": u.wallet.address})
    assert r.status_code == 409 and r.json()["error"] == "last_wallet"
    new_wallet = cc.DevWallet()
    ch = (await client.post("/api/auth/ton-proof/challenge")).json()
    r = await u.post("/api/wallet/connect", new_wallet.proof(ch["payload"], ch["domain"]))
    assert r.status_code == 200 and r.json()["ton_address"] == new_wallet.address
    r = await u.post("/api/wallet/disconnect", {"address": u.wallet.address})
    assert r.status_code == 200 and len(r.json()["wallets"]) == 1

    # теперь вход работает только через новый кошелёк
    again = await login(client, new_wallet)
    assert again["user"]["id"] == u.id


async def test_protected_endpoints_require_token(client):
    for path in ("/api/me", "/api/threads", "/api/settings", "/api/blocklist"):
        r = await client.get(path)
        assert r.status_code == 401, path
    r = await client.get("/api/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


async def test_security_headers_on_pages(client):
    r = await client.get("/login")
    assert r.status_code == 200 and "Connect TON Wallet" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    r = await client.get("/app")
    assert r.status_code == 200
