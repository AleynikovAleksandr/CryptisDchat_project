"""Проверка ECDSA-подписи пакета сообщения (ТЗ 6.2).

Главную проверку делает получатель, но сервер тоже проверяет подпись по
опубликованному Signing-ключу отправителя (защита в глубину): пакет с чужой или
битой подписью не попадёт ни в базу, ни в блокчейн-журнал.

Подписываемые байты (одинаково на клиенте, static/js/lib/crypto.js → signingBytes):
    "cryptis-msg-v1" || 0x00 || thread_id || 0x00 || client_msg_id || 0x00
    || key_epoch (uint32 BE) || nonce (24) || ciphertext
Подпись — IEEE P1363 (r||s, 64 байта), как её выдаёт WebCrypto.
"""
from __future__ import annotations

import base64
import struct

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


def signing_bytes(thread_id: str, client_msg_id: str, key_epoch: int, nonce: bytes, ciphertext: bytes) -> bytes:
    return (
        b"cryptis-msg-v1\x00"
        + thread_id.encode()
        + b"\x00"
        + client_msg_id.encode()
        + b"\x00"
        + struct.pack(">I", key_epoch)
        + nonce
        + ciphertext
    )


def load_p256_public_key(spki_b64: str) -> ec.EllipticCurvePublicKey:
    key = serialization.load_der_public_key(base64.b64decode(spki_b64))
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise ValueError("expected a P-256 public key")
    return key


def is_valid_p256_spki(spki_b64: str) -> bool:
    try:
        load_p256_public_key(spki_b64)
        return True
    except Exception:  # noqa: BLE001
        return False


def verify_p1363(spki_b64: str, signature: bytes, data: bytes) -> bool:
    if len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    try:
        load_p256_public_key(spki_b64).verify(encode_dss_signature(r, s), data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False
