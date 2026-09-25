"""Эталонная реализация клиентской части протокола на Python — для тестов.

Повторяет то, что делает браузер (backend/static/js/lib/crypto.js, treekem.js):
ключи P-256, подпись ECDSA r||s, XSalsa20-Poly1305 (secretbox), ECIES, слепой индекс,
Шамир 2-из-3, TreeKEM-коммиты, ton_proof dev-кошелька.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from nacl.secret import SecretBox
from nacl.signing import SigningKey

from app.services import signatures, treekem
from app.services.ton_proof import build_message
from realm import ecies


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def spki(pub: ec.EllipticCurvePublicKey) -> str:
    return ecies.spki_b64(pub)


def pkcs8(priv: ec.EllipticCurvePrivateKey) -> bytes:
    return priv.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())


# --------------------------------------------------------------------------- ton_proof (dev-кошелёк)
class DevWallet:
    def __init__(self) -> None:
        self.sk = SigningKey.generate()
        self.pub = bytes(self.sk.verify_key)
        self.address = "0:" + hashlib.sha256(self.pub).hexdigest()

    def proof(self, payload: str, domain: str) -> dict:
        ts = int(time.time())
        wc, h = self.address.split(":")
        msg = build_message(int(wc), bytes.fromhex(h), domain, ts, payload)
        sig = self.sk.sign(msg).signature
        return {
            "address": self.address, "network": "-239", "public_key": self.pub.hex(),
            "proof": {"timestamp": ts, "domain": {"lengthBytes": len(domain.encode()), "value": domain},
                      "signature": b64(sig), "payload": payload},
        }


# --------------------------------------------------------------------------- ключи устройства
@dataclass
class DeviceKeys:
    identity: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))
    signing: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))
    version: int = 1

    def public(self) -> dict:
        return {"identity_pub": spki(self.identity.public_key()), "signing_pub": spki(self.signing.public_key())}


def sign_p1363(priv: ec.EllipticCurvePrivateKey, data: bytes) -> bytes:
    r, s = decode_dss_signature(priv.sign(data, ec.ECDSA(hashes.SHA256())))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def search_key(conv_key: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"", info=b"cryptis-search-v1").derive(conv_key)


def search_token(conv_key: bytes, word: str) -> str:
    return hmac.new(search_key(conv_key), word.lower().encode(), hashlib.sha256).hexdigest()[:32]


def word_tokens(conv_key: bytes, text: str) -> list[str]:
    words = {w for w in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split() if len(w) >= 2}
    prefixes = {w[:n] for w in words for n in range(2, min(len(w), 12) + 1)}
    return sorted(search_token(conv_key, p) for p in prefixes)


def make_packet(keys: DeviceKeys, thread_id: str, conv_key: bytes, epoch: int, content: dict,
                kind: str = "text", **extra) -> dict:
    client_msg_id = str(uuid.uuid4())
    nonce = os.urandom(24)
    ct = SecretBox(conv_key).encrypt(json.dumps(content).encode(), nonce).ciphertext
    sig = sign_p1363(keys.signing, signatures.signing_bytes(thread_id, client_msg_id, epoch, nonce, ct))
    packet = {
        "client_msg_id": client_msg_id, "key_epoch": epoch, "sender_key_version": keys.version,
        "nonce": b64(nonce), "ciphertext": b64(ct), "signature": b64(sig), "kind": kind,
        "search_tokens": word_tokens(conv_key, content.get("text", "")),
    }
    packet.update(extra)
    return packet


def decrypt_message(conv_key: bytes, msg: dict) -> dict:
    return json.loads(SecretBox(conv_key).decrypt(base64.b64decode(msg["ciphertext"]), base64.b64decode(msg["nonce"])))


# --------------------------------------------------------------------------- TreeKEM (клиент)
@dataclass
class TreeMember:
    identity: ec.EllipticCurvePrivateKey
    privs: dict[int, ec.EllipticCurvePrivateKey] = field(default_factory=dict)
    epochs: dict[int, bytes] = field(default_factory=dict)

    def set_leaf(self, leaf: int) -> None:
        self.privs.setdefault(treekem.leaf_node(leaf), self.identity)


def build_commit(plan: dict) -> tuple[dict, bytes]:
    fresh = {n: ec.generate_private_key(ec.SECP256R1()) for n in plan["path"]}
    known = {int(k): v for k, v in plan["known_pubs"].items()}
    targets = {int(k): v for k, v in plan["targets"].items()}

    def pub_of(n: int) -> str:
        return spki(fresh[n].public_key()) if n in fresh else known[n]

    nodes = [{
        "index": n, "public_key": spki(fresh[n].public_key()),
        "ciphertexts": [{"target": t, "ct": ecies.seal(pub_of(t), pkcs8(fresh[n]))} for t in targets[n]],
    } for n in plan["path"]]
    epoch_secret = os.urandom(32)
    body = {"epoch": plan["next_epoch"], "nodes": nodes, "root_ciphertext": ecies.seal(pub_of(plan["root"]), epoch_secret)}
    return body, epoch_secret


def process_commit(member: TreeMember, commit: dict) -> bytes | None:
    for node in sorted(commit["nodes"], key=lambda n: (treekem.level(n["index"]), n["index"])):
        new_priv = None
        for c in node["ciphertexts"]:
            holder = member.privs.get(c["target"])
            if holder is not None:
                new_priv = serialization.load_der_private_key(ecies.open_sealed(holder, c["ct"]), password=None)
                break
        if new_priv is not None:
            member.privs[node["index"]] = new_priv
        else:
            member.privs.pop(node["index"], None)
    for b in commit["blanks"]:
        member.privs.pop(b, None)
    root_priv = member.privs.get(commit["root"])
    if root_priv is None:
        return None
    secret = ecies.open_sealed(root_priv, commit["root_ciphertext"])
    member.epochs[commit["epoch"]] = secret
    return secret


# --------------------------------------------------------------------------- резервная копия (6.6)
PRIME_POLY = 0x11B


def _gf_mul(a: int, b: int) -> int:
    p = 0
    while b:
        if b & 1:
            p ^= a
        a <<= 1
        if a & 0x100:
            a ^= PRIME_POLY
        b >>= 1
    return p


def _gf_inv(a: int) -> int:
    return next(x for x in range(1, 256) if _gf_mul(a, x) == 1)


def shamir_split(secret: bytes, n: int, k: int) -> list[bytes]:
    shares = [bytearray([i + 1]) for i in range(n)]
    for byte in secret:
        coeffs = [byte] + [secrets.randbelow(256) for _ in range(k - 1)]
        for i in range(n):
            x, acc, power = i + 1, 0, 1
            for c in coeffs:
                acc ^= _gf_mul(c, power)
                power = _gf_mul(power, x)
            shares[i].append(acc)
    return [bytes(s) for s in shares]


def shamir_combine(shares: list[bytes]) -> bytes:
    xs = [s[0] for s in shares]
    out = bytearray()
    for pos in range(1, len(shares[0])):
        acc = 0
        for i, s in enumerate(shares):
            num, den = 1, 1
            for j, xj in enumerate(xs):
                if i != j:
                    num = _gf_mul(num, xj)
                    den = _gf_mul(den, xs[i] ^ xj)
            acc ^= _gf_mul(s[pos], _gf_mul(num, _gf_inv(den)))
        out.append(acc)
    return bytes(out)


def pin_key(pin: str, salt_b64: str, iterations: int) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(salt_b64),
                      iterations=iterations).derive(pin.encode())


def realm_auth(pk: bytes, realm_index: int) -> bytes:
    return hmac.new(pk, f"realm-auth:{realm_index}".encode(), hashlib.sha256).digest()


def make_backup(bundle: dict, pin: str, realms: list[dict], iterations: int = 100_000) -> tuple[dict, bytes]:
    k = os.urandom(32)
    iv = os.urandom(12)
    ciphertext = iv + AESGCM(k).encrypt(iv, json.dumps(bundle).encode(), None)
    salt = b64(os.urandom(16))
    pk = pin_key(pin, salt, iterations)
    shares = shamir_split(k, len(realms), 2)
    sealed = [{
        "realm_index": r["index"],
        "sealed": ecies.seal(r["public_key"], json.dumps({
            "v": 1, "share": b64(shares[i]), "auth": b64(realm_auth(pk, r["index"])),
        }).encode()),
    } for i, r in enumerate(realms)]
    return {"ciphertext": b64(ciphertext), "kdf_salt": salt, "kdf_iterations": iterations,
            "threshold": 2, "shares": sealed}, k


def recovery_requests(pin: str, meta: dict) -> tuple[list[dict], ec.EllipticCurvePrivateKey]:
    eph = ec.generate_private_key(ec.SECP256R1())
    pk = pin_key(pin, meta["kdf_salt"], meta["kdf_iterations"])
    reqs = [{
        "realm_index": r["index"],
        "sealed_request": ecies.seal(r["public_key"], json.dumps({
            "v": 1, "auth": b64(realm_auth(pk, r["index"])), "client_pub": spki(eph.public_key()),
        }).encode()),
    } for r in meta["realms"]]
    return reqs, eph


def open_backup(meta: dict, shares: list[dict], eph: ec.EllipticCurvePrivateKey) -> dict:
    raw = [base64.b64decode(json.loads(ecies.open_sealed(eph, s["sealed_share"]))["share"]) for s in shares]
    k = shamir_combine(raw[:2])
    blob = base64.b64decode(meta["ciphertext"])
    return json.loads(AESGCM(k).decrypt(blob[:12], blob[12:], None))


def packet_to_proto_fields(packet: dict) -> dict:
    return {
        **{k: packet[k] for k in ("client_msg_id", "key_epoch", "sender_key_version", "kind")},
        "nonce": base64.b64decode(packet["nonce"]), "ciphertext": base64.b64decode(packet["ciphertext"]),
        "signature": base64.b64decode(packet["signature"]), "search_tokens": packet.get("search_tokens", []),
    }

