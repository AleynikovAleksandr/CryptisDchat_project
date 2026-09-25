"""Проверка владения TON-адресом по схеме ton_proof (ТЗ 4.2)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class TonProofRequest:
    address: str            # raw "0:<hex>" от кошелька
    network: str
    public_key: str         # ed25519 hex
    timestamp: int
    domain: str
    domain_length: int
    payload: str
    signature: str          # base64
    state_init: str | None  # base64 BOC, если кошелёк прислал


@dataclass(frozen=True)
class VerifiedWallet:
    address: str
    friendly_address: str
    public_key: str
    network: str


class ITonProofVerifier(ABC):
    @abstractmethod
    def verify(self, proof: TonProofRequest) -> VerifiedWallet:
        """Проверить подпись и привязку ключа к адресу; при ошибке — TonProofError."""
