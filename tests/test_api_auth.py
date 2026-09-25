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



async def test_device_cookie_is_httponly_and_scoped_to_auth(client):
    data = await login(client)
    cookie = data["_set_cookie"].lower()
    assert data["_device"] and "httponly" in cookie and "samesite=strict" in cookie and "path=/api/auth" in cookie


async def test_same_browser_relogin_reuses_device(client):
    wallet = cc.DevWallet()
    a = await login(client, wallet)
    b = await login(client, wallet, device_cookie=a["_device"])
    hb = {"Authorization": f"Bearer {b['access_token']}"}
    sessions = (await client.get("/api/auth/sessions", headers=hb)).json()
    assert len(sessions) == 1 and sessions[0]["current"] is True
    # у устройства один действующий refresh-токен: прежний погашен без признака кражи
    r = await client.post("/api/auth/refresh", json={"refresh_token": a["refresh_token"]})
    assert r.status_code == 401 and r.json()["error"] == "refresh_revoked"
    assert (await client.post("/api/auth/refresh", json={"refresh_token": b["refresh_token"]})).status_code == 200

    # другой браузер (без cookie) того же пользователя — отдельное устройство
    await login(client, wallet)
    assert len((await client.get("/api/auth/sessions", headers=hb)).json()) == 2


async def test_device_cookie_rotates_on_every_login(client):
    wallet = cc.DevWallet()
    a = await login(client, wallet)
    b = await login(client, wallet, device_cookie=a["_device"])
    assert b["_device"] != a["_device"]
    # старая (например, украденная) cookie устройство больше не узнаёт
    c = await login(client, wallet, device_cookie=a["_device"])
    sessions = (await client.get("/api/auth/sessions", headers={"Authorization": f"Bearer {c['access_token']}"})).json()
    assert len(sessions) == 2


async def test_device_cookie_is_scoped_to_user(client):
    a = await login(client, cc.DevWallet())
    b = await login(client, cc.DevWallet(), device_cookie=a["_device"])
    sa = (await client.get("/api/auth/sessions", headers={"Authorization": f"Bearer {a['access_token']}"})).json()
    sb = (await client.get("/api/auth/sessions", headers={"Authorization": f"Bearer {b['access_token']}"})).json()
    assert len(sa) == 1 and len(sb) == 1 and sa[0]["device_id"] != sb[0]["device_id"]
    # чужая cookie не трогает устройство первого пользователя
    assert (await client.post("/api/auth/refresh", json={"refresh_token": a["refresh_token"]})).status_code == 200


async def test_relogin_after_logout_revives_device_but_not_old_tokens(client):
    wallet = cc.DevWallet()
    a = await login(client, wallet)
    ha = {"Authorization": f"Bearer {a['access_token']}"}
    device_id = (await client.get("/api/auth/sessions", headers=ha)).json()[0]["device_id"]
    assert (await client.post("/api/auth/logout", headers=ha)).status_code == 204

    b = await login(client, wallet, device_cookie=a["_device"])
    hb = {"Authorization": f"Bearer {b['access_token']}"}
    sessions = (await client.get("/api/auth/sessions", headers=hb)).json()
    assert [s["device_id"] for s in sessions] == [device_id]
    # JWT, выданный до выхода, остаётся отозванным
    r = await client.get("/api/me", headers=ha)
    assert r.status_code == 401 and r.json()["error"] == "token_revoked"
    assert (await client.post("/api/auth/refresh", json={"refresh_token": a["refresh_token"]})).status_code == 401


async def test_garbage_device_cookie_creates_new_device(client):
    data = await login(client, device_cookie="x" * 500)
    sessions = (await client.get("/api/auth/sessions",
                                 headers={"Authorization": f"Bearer {data['access_token']}"})).json()
    assert len(sessions) == 1


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
