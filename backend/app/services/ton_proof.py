"""Проверка ton_proof (ТЗ 4.2) по спецификации TonConnect.

Подписываемое кошельком сообщение:
    message = "ton-proof-item-v2/" || workchain(int32 BE) || address_hash(32)
              || domain_len(uint32 LE) || domain || timestamp(uint64 LE) || payload
    signature = Ed25519(sha256(0xffff || "ton-connect" || sha256(message)))

Проверки:
  1. payload выдан нашим сервером, не истёк и используется один раз (одноразовость — в Redis);
  2. домен совпадает с доменом приложения, timestamp свежий;
  3. публичный ключ действительно принадлежит адресу: хэш state_init кошелька равен
     хэшу адреса, а ключ извлечён из данных контракта (v3r2 / v4r2 / v5r1);
  4. подпись Ed25519 верна.

В dev-режиме (`DEV_WALLET_LOGIN=true`) вместо state_init допускается «адрес разработчика»
0:sha256(public_key) — это позволяет войти без настоящего кошелька, но подпись всё равно
проверяется полностью.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.config import Settings
from app.interfaces.ton_proof import ITonProofVerifier, TonProofRequest, VerifiedWallet


class TonProofError(Exception):
    pass


# ----------------------------------------------------------------------------- payload
def make_payload(secret: str, ttl: int) -> str:
    """Одноразовый payload: 16 случайных байт + срок действия + HMAC. Stateless-проверка подлинности,
    одноразовость обеспечивает Redis (см. AuthService)."""
    nonce = secrets.token_bytes(16)
    expires = int(time.time()) + ttl
    body = nonce + struct.pack(">Q", expires)
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()[:16]
    return (body + mac).hex()


def check_payload(secret: str, payload: str) -> None:
    try:
        raw = bytes.fromhex(payload)
    except ValueError as exc:
        raise TonProofError("malformed payload") from exc
    if len(raw) != 40:
        raise TonProofError("malformed payload")
    body, mac = raw[:24], raw[24:]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(mac, expected):
        raise TonProofError("payload was not issued by this server")
    (expires,) = struct.unpack(">Q", body[16:])
    if expires < time.time():
        raise TonProofError("payload expired")


# ----------------------------------------------------------------------------- адреса
def parse_raw_address(address: str) -> tuple[int, bytes]:
    try:
        wc_str, hash_hex = address.split(":", 1)
        wc = int(wc_str)
        addr_hash = bytes.fromhex(hash_hex)
    except ValueError as exc:
        raise TonProofError("address must be in raw form <wc>:<hex>") from exc
    if len(addr_hash) != 32 or wc not in (0, -1):
        raise TonProofError("bad address")
    return wc, addr_hash


def _crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def to_friendly(address: str, testnet: bool = False, bounceable: bool = False) -> str:
    """user-friendly адрес (base64url) — так он показывается в интерфейсе."""
    wc, addr_hash = parse_raw_address(address)
    tag = 0x51 if not bounceable else 0x11
    if testnet:
        tag |= 0x80
    data = bytes([tag, wc & 0xFF]) + addr_hash
    data += struct.pack(">H", _crc16_xmodem(data))
    return base64.urlsafe_b64encode(data).decode()


def build_message(wc: int, addr_hash: bytes, domain: str, timestamp: int, payload: str) -> bytes:
    domain_b = domain.encode()
    msg = (
        b"ton-proof-item-v2/"
        + struct.pack(">i", wc)
        + addr_hash
        + struct.pack("<I", len(domain_b))
        + domain_b
        + struct.pack("<Q", timestamp)
        + payload.encode()
    )
    return hashlib.sha256(b"\xff\xff" + b"ton-connect" + hashlib.sha256(msg).digest()).digest()


# ----------------------------------------------------------------------------- state_init
def _pubkey_from_state_init(state_init_b64: str, addr_hash: bytes) -> bytes:
    """Проверить, что state_init соответствует адресу, и достать ed25519-ключ из данных кошелька."""
    from tonsdk.boc import Cell  # импорт здесь: тяжёлая зависимость нужна только при входе

    try:
        cell = Cell.one_from_boc(base64.b64decode(state_init_b64))
    except Exception as exc:  # noqa: BLE001 — любой мусор во входных данных
        raise TonProofError("cannot parse state_init") from exc
    if cell.bytes_hash() != addr_hash:
        raise TonProofError("state_init does not match address")
    if len(cell.refs) < 2:
        raise TonProofError("state_init has no data cell")
    data = bytes(cell.refs[1].bits.array)
    bit_len = cell.refs[1].bits.cursor
    candidates: list[bytes] = []
    # v3r2 / v4r2: seqno(32) | subwallet_id(32) | public_key(256) ...
    if bit_len >= 320:
        candidates.append(data[8:40])
    # v5r1: is_signature_allowed(1) | seqno(32) | wallet_id(32) | public_key(256) ...
    if bit_len >= 321:
        as_int = int.from_bytes(data[:41].ljust(41, b"\x00"), "big")
        total_bits = 41 * 8
        shift = total_bits - (1 + 32 + 32 + 256)
        candidates.append(((as_int >> shift) & ((1 << 256) - 1)).to_bytes(32, "big"))
    if not candidates:
        raise TonProofError("unsupported wallet contract")
    return b"".join(candidates)  # вызывающий сверит с заявленным ключом


class TonProofVerifier(ITonProofVerifier):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify(self, proof: TonProofRequest) -> VerifiedWallet:
        s = self.settings
        check_payload(s.ton_proof_secret, proof.payload)

        if proof.domain != s.ton_proof_domain or proof.domain_length != len(proof.domain.encode()):
            raise TonProofError("domain mismatch")
        if abs(time.time() - proof.timestamp) > s.ton_proof_ttl_seconds:
            raise TonProofError("proof timestamp is too old")

        wc, addr_hash = parse_raw_address(proof.address)
        try:
            pubkey = bytes.fromhex(proof.public_key)
        except ValueError as exc:
            raise TonProofError("bad public key") from exc
        if len(pubkey) != 32:
            raise TonProofError("bad public key")

        if proof.state_init:
            extracted = _pubkey_from_state_init(proof.state_init, addr_hash)
            if pubkey not in [extracted[i:i + 32] for i in range(0, len(extracted), 32)]:
                raise TonProofError("public key does not belong to the wallet")
        elif s.dev_wallet_login and s.is_dev:
            if hashlib.sha256(pubkey).digest() != addr_hash:
                raise TonProofError("dev address must be sha256(public_key)")
        else:
            raise TonProofError("state_init is required")

        message = build_message(wc, addr_hash, proof.domain, proof.timestamp, proof.payload)
        try:
            signature = base64.b64decode(proof.signature)
            VerifyKey(pubkey).verify(message, signature)
        except (BadSignatureError, ValueError) as exc:
            raise TonProofError("invalid signature") from exc

        testnet = proof.network == "-3"
        return VerifiedWallet(
            address=f"{wc}:{addr_hash.hex()}",
            friendly_address=to_friendly(proof.address, testnet=testnet),
            public_key=pubkey.hex(),
            network=proof.network,
        )
