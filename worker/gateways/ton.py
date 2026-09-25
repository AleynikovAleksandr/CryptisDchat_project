"""Шлюзы к блокчейну TON (Gunicorn_Celery.md 4.2–4.4).

Транзакции отправляет СЛУЖЕБНЫЙ кошелёк приложения, а не кошелёк пользователя.
Каждая транзакция — перевод самому себе с текстовым комментарием-«якорем»:
    cryptis:v1:batch:<merkle_root>          — батч подтверждений сообщений
    cryptis:v1:group:<thread_id>:<hash>     — изменение состава группы

Мнемоника служебного кошелька доступна только контейнеру воркера (worker.env),
API-процессу — нет (ТЗ 7: наименьшие привилегии).
"""
from __future__ import annotations

import hashlib
import logging
import time

import httpx
import redis

from app.config import Settings
from app.interfaces.blockchain import AnchorResult, ConfirmationResult, IBlockchainGateway

log = logging.getLogger(__name__)


class MockBlockchainGateway(IBlockchainGateway):
    """Эмуляция сети для разработки: «подтверждение» наступает через несколько секунд."""

    CONFIRM_AFTER = 3.0

    def __init__(self, r: redis.Redis) -> None:
        self.r = r

    def anchor(self, comment: str, idempotency_key: str) -> AnchorResult:
        idem = f"mockchain:idem:{idempotency_key}"
        existing = self.r.get(idem)
        if existing:
            return AnchorResult(tx_ref=existing, submitted=True)
        tx_ref = "mock:" + hashlib.sha256(comment.encode()).hexdigest()
        self.r.set(f"mockchain:tx:{tx_ref}", str(time.time()))
        self.r.set(idem, tx_ref)
        return AnchorResult(tx_ref=tx_ref, submitted=True)

    def check(self, tx_ref: str) -> ConfirmationResult:
        submitted = self.r.get(f"mockchain:tx:{tx_ref}")
        if submitted is None or time.time() - float(submitted) < self.CONFIRM_AFTER:
            return ConfirmationResult(confirmed=False)
        tx_hash = hashlib.sha256(("tx" + tx_ref).encode()).hexdigest()
        return ConfirmationResult(confirmed=True, tx_hash=tx_hash, lt=int(float(submitted) * 1000), confirmations=1)


class ToncenterGateway(IBlockchainGateway):
    """Реальная сеть через toncenter HTTP API v2 и кошелёк tonsdk (v3r2 / v4r2).

    Идемпотентность: перед отправкой ищем среди последних транзакций кошелька исходящее
    сообщение с тем же комментарием — повтор после сбоя не создаст вторую транзакцию.
    """

    SCAN_LIMIT = 50

    def __init__(self, settings: Settings) -> None:
        from tonsdk.contract.wallet import Wallets, WalletVersionEnum

        words = settings.service_wallet_mnemonic.split()
        if len(words) != 24:
            raise RuntimeError("SERVICE_WALLET_MNEMONIC must contain 24 words")
        _, _, _, self.wallet = Wallets.from_mnemonics(words, WalletVersionEnum(settings.service_wallet_version), 0)
        self.address = self.wallet.address.to_string(True, True, True)
        headers = {"X-API-Key": settings.toncenter_api_key} if settings.toncenter_api_key else {}
        self.http = httpx.Client(base_url=settings.toncenter_endpoint, headers=headers, timeout=20)

    def _call(self, method: str, path: str, **kwargs) -> dict:
        resp = self.http.request(method, path, **kwargs)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok", False):
            raise RuntimeError(f"toncenter error: {body.get('error')}")
        return body["result"]

    def _seqno(self) -> int:
        result = self._call("POST", "/runGetMethod", json={"address": self.address, "method": "seqno", "stack": []})
        if result.get("exit_code") not in (0, None):
            return 0  # кошелёк ещё не развёрнут — первая транзакция развернёт его (state_init)
        return int(result["stack"][0][1], 16)

    def _find(self, comment: str) -> ConfirmationResult:
        txs = self._call("GET", "/getTransactions", params={"address": self.address, "limit": self.SCAN_LIMIT})
        for tx in txs:
            for out in tx.get("out_msgs", []):
                if out.get("message") == comment:
                    tid = tx["transaction_id"]
                    return ConfirmationResult(confirmed=True, tx_hash=tid["hash"], lt=int(tid["lt"]), confirmations=1)
        return ConfirmationResult(confirmed=False)

    def anchor(self, comment: str, idempotency_key: str) -> AnchorResult:
        from tonsdk.utils import bytes_to_b64str, to_nano

        if self._find(comment).confirmed:
            return AnchorResult(tx_ref=comment, submitted=True)
        query = self.wallet.create_transfer_message(
            to_addr=self.address, amount=to_nano(0.001, "ton"), seqno=self._seqno(), payload=comment, send_mode=3,
        )
        boc = bytes_to_b64str(query["message"].to_boc(False))
        self._call("POST", "/sendBoc", json={"boc": boc})
        log.info("anchored %s (key %s)", comment, idempotency_key)
        return AnchorResult(tx_ref=comment, submitted=True)

    def check(self, tx_ref: str) -> ConfirmationResult:
        return self._find(tx_ref)
