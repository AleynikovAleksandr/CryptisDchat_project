"""ECIES на P-256 — тот же формат, что в браузере (backend/static/js/lib/crypto.js → ecies*).

    shared = ECDH(ephemeral_priv, recipient_pub)                  (32 байта, координата X)
    key    = HKDF-SHA256(shared, salt=ephemeral_pub_raw, info="cryptis-ecies-v1", 16 байт)
    blob   = 0x01 || ephemeral_pub_raw (65) || iv (12) || AES-128-GCM(key, iv, plaintext)

AES-128-GCM и эфемерный ECDH — как требует ТЗ 6.3 для обёртки Conversation key.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = 1
INFO = b"cryptis-ecies-v1"


def _raw(pub: ec.EllipticCurvePublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _kdf(shared: bytes, eph_raw: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=16, salt=eph_raw, info=INFO).derive(shared)


def load_spki(spki_b64: str) -> ec.EllipticCurvePublicKey:
    key = serialization.load_der_public_key(base64.b64decode(spki_b64))
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise ValueError("expected P-256 public key")
    return key


def spki_b64(pub: ec.EllipticCurvePublicKey) -> str:
    der = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


def seal(recipient_spki_b64: str, plaintext: bytes) -> str:
    recipient = load_spki(recipient_spki_b64)
    eph = ec.generate_private_key(ec.SECP256R1())
    eph_raw = _raw(eph.public_key())
    key = _kdf(eph.exchange(ec.ECDH(), recipient), eph_raw)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext, None)
    return base64.b64encode(bytes([VERSION]) + eph_raw + iv + ct).decode()


def open_sealed(private_key: ec.EllipticCurvePrivateKey, blob_b64: str) -> bytes:
    blob = base64.b64decode(blob_b64)
    if len(blob) < 1 + 65 + 12 + 16 or blob[0] != VERSION:
        raise ValueError("bad ECIES blob")
    eph_raw, iv, ct = blob[1:66], blob[66:78], blob[78:]
    eph = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), eph_raw)
    key = _kdf(private_key.exchange(ec.ECDH(), eph), eph_raw)
    return AESGCM(key).decrypt(iv, ct, None)
