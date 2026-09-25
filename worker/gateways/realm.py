"""HTTP-клиент realm-сервиса (realm/app.py). Доступен только из изолированной сети keys_network."""
from __future__ import annotations

import httpx

from app.interfaces.realm import IRealmClient, RealmRecoverResult


class HttpRealmClient(IRealmClient):
    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.http = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout)

    def public_key(self) -> str:
        resp = self.http.get("/v1/public-key")
        resp.raise_for_status()
        return resp.json()["public_key"]

    def store(self, user_id: str, version: int, sealed: str) -> None:
        resp = self.http.put(f"/v1/shares/{user_id}", json={"version": version, "sealed": sealed})
        resp.raise_for_status()

    def recover(self, user_id: str, sealed_request: str) -> RealmRecoverResult:
        resp = self.http.post(f"/v1/shares/{user_id}/recover", json={"sealed_request": sealed_request})
        if resp.status_code == 404:
            return RealmRecoverResult(status="not_found")
        resp.raise_for_status()
        body = resp.json()
        return RealmRecoverResult(status=body["status"], sealed_share=body.get("sealed_share"),
                                  attempts_left=body.get("attempts_left"))

    def destroy(self, user_id: str) -> None:
        resp = self.http.delete(f"/v1/shares/{user_id}")
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()
