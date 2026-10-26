"""Резервное копирование ключей по Шамиру 2-из-3 на трёх realm-сервисах (ТЗ 6.6)."""
from __future__ import annotations

from tests import client_crypto as cc


async def publish_realm_keys(infra) -> None:
    infra.tasks.enqueue("keys.refresh_realm_keys")


async def create_backup(user, infra, pin="1234") -> dict:
    await publish_realm_keys(infra)
    realms = (await user.get("/api/keys/realms")).json()
    assert len(realms) == 3
    bundle = {"identity": "pkcs8-identity-b64", "signing": "pkcs8-signing-b64"}
    body, _ = cc.make_backup(bundle, pin, realms)
    r = await user.post("/api/keys/backup", body)
    assert r.status_code == 201, r.text
    return bundle


async def recover(user, pin: str, realm_indices: tuple[int, ...] | None = None) -> tuple[dict, object, dict]:
    meta = (await user.get("/api/keys/backup")).json()
    reqs, eph = cc.recovery_requests(pin, meta)
    if realm_indices is not None:
        reqs = [r for r in reqs if r["realm_index"] in realm_indices]
    started = (await user.post("/api/keys/recovery", {"requests": reqs})).json()
    status = (await user.get(f"/api/keys/recovery/{started['id']}")).json()
    return status, eph, meta


async def test_backup_and_restore_on_new_device(make_user, infra, client):
    user = await make_user()
    bundle = await create_backup(user, infra)
    meta = (await user.get("/api/keys/backup")).json()
    assert meta["status"] == "active" and meta["threshold"] == 2
    assert (await user.get("/api/me")).json()["has_backup"] is True

    # новое устройство: вход тем же кошельком, ключей локально нет
    from tests.conftest import User, login
    data = await login(client, user.wallet)
    new_device = User(client, data, user.wallet)
    status, eph, meta = await recover(new_device, "1234")
    assert status["status"] == "done" and len(status["shares"]) >= 2
    assert cc.open_backup(meta, status["shares"], eph) == bundle


async def test_tenth_wrong_pin_locks_recovery_for_fifteen_minutes(make_user, infra, realms, tmp_path, monkeypatch):
    user = await make_user()
    await create_backup(user, infra)
    from realm import app as realm_app
    from worker.tasks import keys as key_tasks
    now = 1_800_000_000.0
    from types import SimpleNamespace
    monkeypatch.setattr(realm_app, "time", SimpleNamespace(time=lambda: now))
    monkeypatch.setattr(key_tasks, "time", SimpleNamespace(time=lambda: now))
    status, _, _ = await recover(user, "0000")
    assert status["status"] == "wrong_pin" and status["attempts_left"] == 9
    for _ in range(8):
        status, _, _ = await recover(user, "0000")
    assert status["attempts_left"] == 1
    status, _, _ = await recover(user, "9999")
    assert status["status"] == "locked" and status["locked_until"] == now + 15 * 60
    assert (await user.get("/api/keys/backup")).json()["status"] == "active"
    from fastapi.testclient import TestClient
    fresh_realm = TestClient(realm_app.create_app(tmp_path / "realm0", api_token="t", max_attempts=10),
                             headers={"Authorization": "Bearer t"})
    meta = (await user.get("/api/keys/backup")).json()
    sealed, _ = cc.recovery_requests("1234", meta)
    response = fresh_realm.post(f"/v1/shares/{user.id}/recover",
                                json={"sealed_request": sealed[0]["sealed_request"]})
    assert response.json()["status"] == "locked"
    status, _, _ = await recover(user, "1234")
    assert status["status"] == "locked" and status["locked_until"] == now + 15 * 60
    now += 15 * 60
    status, eph, meta = await recover(user, "1234")
    assert status["status"] == "done"
    assert cc.open_backup(meta, status["shares"], eph)["identity"] == "pkcs8-identity-b64"


async def test_correct_pin_resets_attempt_counter(make_user, infra):
    user = await make_user()
    await create_backup(user, infra)
    for _ in range(5):
        await recover(user, "1111")
    status, _, _ = await recover(user, "1234")
    assert status["status"] == "done"
    status, _, _ = await recover(user, "1111")
    assert status["attempts_left"] == 9


async def test_rotating_realm_pairs_cannot_exceed_ten_wrong_pins(make_user, infra):
    user = await make_user()
    await create_backup(user, infra)
    pairs = ((0, 1), (1, 2), (0, 2))
    for attempt in range(10):
        status, _, _ = await recover(user, "0000", pairs[attempt % len(pairs)])
        if attempt < 9:
            assert status["status"] == "wrong_pin"
            assert status["attempts_left"] == 9 - attempt
    assert status["status"] == "locked" and status["attempts_left"] == 0
    status, _, _ = await recover(user, "1234", (0, 1))
    assert status["status"] == "locked"


async def test_backup_survives_one_realm_outage(make_user, infra, realms):
    user = await make_user()
    bundle = await create_backup(user, infra)

    class Down:
        def recover(self, *_):
            raise ConnectionError("realm offline")

    realms[1] = Down()  # один из трёх realm недоступен — 2 из 3 достаточно
    status, eph, meta = await recover(user, "1234")
    assert status["status"] == "done"
    assert cc.open_backup(meta, status["shares"], eph) == bundle


async def test_backend_never_sees_share_plaintext(make_user, infra):
    user = await make_user()
    await create_backup(user, infra)
    calls = [c for c in infra.tasks.calls if c[0] == "keys.store_shares"]
    shares = calls[0][1][2]
    for s in shares:
        assert "share" not in s["sealed"] and "auth" not in s["sealed"]  # только ECIES-блоб
