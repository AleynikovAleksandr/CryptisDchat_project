"""Web Push: шифрование RFC 8291, подпись VAPID, проверка подписок и отправка."""
from __future__ import annotations

import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.config import get_settings
from app.interfaces.push import PushMessage
from app.services import webpush
from app.services.webpush import Subscription, b64u, unb64u
from worker.gateways.push import WebPushSender


def make_subscription(endpoint: str = "https://fcm.googleapis.com/fcm/send/abc") -> dict:
    ua = ec.generate_private_key(ec.SECP256R1())
    pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": endpoint, "keys": {"p256dh": b64u(pub), "auth": b64u(b"0123456789abcdef")}}


def test_encrypt_matches_rfc8291_vector():
    """RFC 8291, приложение A."""
    as_priv = ec.derive_private_key(
        int.from_bytes(unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    sub = Subscription(
        "https://push.example.net",
        unb64u("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"),
        unb64u("BTBZMqHH6r4Tts7J_aSIgg"),
    )
    body = webpush.encrypt(sub, b"When I grow up, I want to be a watermelon",
                           ephemeral=as_priv, salt=unb64u("DGv6ra1nlYgDCS1FRnbzlw"))
    assert b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8w"
        "EqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_vapid_jwt_is_signed_for_push_origin():
    key = webpush.generate_vapid_private_key()
    headers = webpush.vapid_headers("https://fcm.googleapis.com/fcm/send/abc", key, "mailto:a@b.c")
    token, k = headers["Authorization"].removeprefix("vapid t=").split(", k=")
    assert k == webpush.vapid_public_key(key)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(k))
    claims = jwt.decode(token, pub, algorithms=["ES256"], audience="https://fcm.googleapis.com")
    assert claims["sub"] == "mailto:a@b.c"


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/x",            # не https
    "https://127.0.0.1/x",                    # внутренняя сеть
    "https://evil.example.com/x",             # не push-сервис
    "https://fcm.googleapis.com.evil.com/x",  # поддомен чужого хоста
    "https://fcm.googleapis.com:8443/x",      # чужой порт
])
def test_subscription_rejects_non_push_endpoints(endpoint):
    with pytest.raises(ValueError):
        webpush.parse_subscription(json.dumps(make_subscription(endpoint)))


def test_subscription_accepts_browser_push_services():
    for endpoint in ("https://fcm.googleapis.com/fcm/send/x", "https://updates.push.services.mozilla.com/wpush/v2/x",
                     "https://web.push.apple.com/QH", "https://wns2-par02p.notify.windows.com/w/?token=x"):
        assert webpush.parse_subscription(json.dumps(make_subscription(endpoint))).endpoint == endpoint


def sender_with(handler) -> WebPushSender:
    return WebPushSender(webpush.generate_vapid_private_key(), "mailto:a@b.c", fallback=None,  # type: ignore[arg-type]
                         client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_sender_posts_encrypted_payload_and_drops_gone_subscriptions():
    seen = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201)

    msg = PushMessage(token=json.dumps(make_subscription()), platform="webpush", title="Alice", body="New message",
                      data={"thread_id": "t1"})
    assert sender_with(ok).send(msg) is True
    req = seen[0]
    assert req.headers["Content-Encoding"] == "aes128gcm" and req.headers["Authorization"].startswith("vapid t=")
    assert b"Alice" not in req.content  # полезная нагрузка зашифрована для браузера
    assert sender_with(lambda r: httpx.Response(410)).send(msg) is False


async def test_push_token_api_validates_webpush_and_serves_vapid_key(make_user, infra, monkeypatch):
    user = await make_user()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "")  # переменная окружения важнее ключа из локального .env
    get_settings.cache_clear()
    infra.settings = get_settings()
    assert (await user.get("/api/push/vapid-public-key")).status_code == 404  # не настроен
    key = webpush.generate_vapid_private_key()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", key)
    get_settings.cache_clear()
    infra.settings = get_settings()
    assert (await user.get("/api/push/vapid-public-key")).json() == {"public_key": webpush.vapid_public_key(key)}

    bad = json.dumps(make_subscription("https://169.254.169.254/latest"))
    assert (await user.post("/api/me/push-token", {"platform": "webpush", "token": bad})).status_code == 422
    good = json.dumps(make_subscription())
    assert (await user.post("/api/me/push-token", {"platform": "webpush", "token": good})).status_code == 204
