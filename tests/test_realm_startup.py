"""Параллельные запросы к новому realm не должны создавать ключ одновременно."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from realm import app as realm_app


def test_realm_initializes_store_once_for_concurrent_requests(tmp_path, monkeypatch):
    original = realm_app.Store
    constructions = 0
    counter_lock = threading.Lock()

    class SlowStore(original):
        def __init__(self, data_dir):
            nonlocal constructions
            with counter_lock:
                constructions += 1
            time.sleep(0.05)
            super().__init__(data_dir)

    monkeypatch.setattr(realm_app, "Store", SlowStore)
    app = realm_app.create_app(data_dir=tmp_path / "realm", api_token="test")
    barrier = threading.Barrier(8)

    def public_key():
        barrier.wait()
        with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
            return client.get("/v1/public-key").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: public_key(), range(8)))

    assert statuses == [200] * 8
    assert constructions == 1
