"""Web Push без сторонних библиотек: шифрование полезной нагрузки (RFC 8291, aes128gcm)
и подпись запроса VAPID (RFC 8292).

Сервер не знает текста сообщений, поэтому в push уходят только заголовок (имя отправителя)
и нейтральное «New message»; шифрование RFC 8291 скрывает и их от push-сервиса браузера.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

RECORD_SIZE = 4096
VAPID_TTL_SECONDS = 12 * 3600

# Push-сервисы браузеров. Адрес подписки приходит от клиента, и сервер шлёт на него POST —
# без списка разрешённых хостов это был бы SSRF во внутреннюю сеть.
PUSH_HOSTS = (
    "fcm.googleapis.com",                 # Chrome, Edge (Chromium), Opera
    "updates.push.services.mozilla.com",  # Firefox
    ".push.apple.com",                    # Safari
    ".notify.windows.com",                # старый Edge / Windows
)


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class Subscription:
    endpoint: str
    p256dh: bytes   # открытый ключ браузера, несжатая точка P-256 (65 байт)
    auth: bytes     # секрет аутентификации (16 байт)


def push_host_allowed(host: str) -> bool:
    host = host.lower()
    return any(host.endswith(h) if h.startswith(".") else host == h for h in PUSH_HOSTS)


def parse_subscription(token: str) -> Subscription:
    """Разобрать PushSubscription.toJSON() из push_tokens.token; ValueError — подписка негодная."""
    try:
        data = json.loads(token)
        endpoint = str(data["endpoint"])
        p256dh = unb64u(data["keys"]["p256dh"])
        auth = unb64u(data["keys"]["auth"])
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError("malformed push subscription") from e
    url = urlsplit(endpoint)
    if url.scheme != "https" or not url.hostname or not push_host_allowed(url.hostname) or url.port not in (None, 443):
        raise ValueError("push endpoint is not a known push service")
    if len(p256dh) != 65 or p256dh[0] != 4 or len(auth) != 16:
        raise ValueError("bad push subscription keys")
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)  # точка на кривой
    return Subscription(endpoint, p256dh, auth)


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _raw_public(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(sub: Subscription, payload: bytes, *, ephemeral: ec.EllipticCurvePrivateKey | None = None,
            salt: bytes | None = None) -> bytes:
    """Тело запроса Content-Encoding: aes128gcm (RFC 8291, раздел 3–4), одна запись."""
    if len(payload) > RECORD_SIZE - 17:  # запись = данные + разделитель 0x02 + тег GCM (16)
        raise ValueError("push payload too large")
    ephemeral = ephemeral or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = _raw_public(ephemeral)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sub.p256dh)
    ecdh_secret = ephemeral.exchange(ec.ECDH(), ua_public)

    prk_key = _hmac(sub.auth, ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + sub.p256dh + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02 — последняя запись
    header = salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + ciphertext


def load_vapid_key(private_b64u: str) -> ec.EllipticCurvePrivateKey:
    """VAPID_PRIVATE_KEY — 32 байта скаляра P-256 в base64url (формат web-push)."""
    raw = unb64u(private_b64u.strip())
    if len(raw) != 32:
        raise ValueError("VAPID_PRIVATE_KEY must be 32 bytes in base64url")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def vapid_public_key(private_b64u: str) -> str:
    """applicationServerKey для pushManager.subscribe()."""
    return b64u(_raw_public(load_vapid_key(private_b64u)))


def generate_vapid_private_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return b64u(key.private_numbers().private_value.to_bytes(32, "big"))


def vapid_headers(endpoint: str, private_b64u: str, subject: str, now: float | None = None) -> dict[str, str]:
    url = urlsplit(endpoint)
    claims = {
        "aud": f"{url.scheme}://{url.netloc}",
        "exp": int((now or time.time()) + VAPID_TTL_SECONDS),
        "sub": subject,
    }
    token = jwt.encode(claims, load_vapid_key(private_b64u), algorithm="ES256")
    return {"Authorization": f"vapid t={token}, k={vapid_public_key(private_b64u)}"}
