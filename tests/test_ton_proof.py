"""ton_proof с настоящим контрактом кошелька (tonsdk v4r2): state_init → адрес → ключ → подпись."""
from __future__ import annotations

import base64
import time

import pytest
from nacl.signing import SigningKey

from app.config import Settings
from app.interfaces.ton_proof import TonProofRequest
from app.services.ton_proof import TonProofError, TonProofVerifier, build_message, make_payload


def real_wallet():
    from tonsdk.contract.wallet import Wallets, WalletVersionEnum

    mnemonics, pub, priv, wallet = Wallets.create(WalletVersionEnum.v4r2, 0)
    state_init = wallet.create_state_init()["state_init"]
    address = wallet.address.to_string(False)  # raw "0:hex"
    seed = bytes(priv)[:32]
    return address, bytes(pub), SigningKey(seed), base64.b64encode(state_init.to_boc(False)).decode()


def settings(**kw) -> Settings:
    base = dict(app_env="production", ton_proof_domain="cryptis.example", ton_proof_secret="k", dev_wallet_login=False)
    base.update(kw)
    return Settings(**base)


def make_request(address, pub, sk, state_init, s: Settings, **override) -> TonProofRequest:
    payload = make_payload(s.ton_proof_secret, 600)
    ts = int(time.time())
    wc, h = address.split(":")
    sig = sk.sign(build_message(int(wc), bytes.fromhex(h), s.ton_proof_domain, ts, payload)).signature
    fields = dict(address=address, network="-239", public_key=pub.hex(), timestamp=ts, domain=s.ton_proof_domain,
                  domain_length=len(s.ton_proof_domain), payload=payload, signature=base64.b64encode(sig).decode(),
                  state_init=state_init)
    fields.update(override)
    return TonProofRequest(**fields)


def test_real_v4r2_wallet_proof_is_accepted():
    s = settings()
    address, pub, sk, state_init = real_wallet()
    verified = TonProofVerifier(s).verify(make_request(address, pub, sk, state_init, s))
    assert verified.address == address and verified.public_key == pub.hex()


def test_foreign_public_key_is_rejected():
    s = settings()
    address, _, _, state_init = real_wallet()
    attacker = SigningKey.generate()
    req = make_request(address, bytes(attacker.verify_key), attacker, state_init, s)
    with pytest.raises(TonProofError, match="does not belong"):
        TonProofVerifier(s).verify(req)


def test_state_init_of_another_wallet_is_rejected():
    s = settings()
    address, pub, sk, _ = real_wallet()
    _, _, _, other_state_init = real_wallet()
    with pytest.raises(TonProofError, match="does not match address"):
        TonProofVerifier(s).verify(make_request(address, pub, sk, other_state_init, s))


def test_wrong_domain_and_bad_signature_are_rejected():
    s = settings()
    address, pub, sk, state_init = real_wallet()
    with pytest.raises(TonProofError, match="domain"):
        TonProofVerifier(s).verify(make_request(address, pub, sk, state_init, s, domain="evil.example",
                                                domain_length=12))
    req = make_request(address, pub, sk, state_init, s)
    forged = TonProofRequest(**{**req.__dict__, "signature": base64.b64encode(b"\x00" * 64).decode()})
    with pytest.raises(TonProofError, match="signature"):
        TonProofVerifier(s).verify(forged)


def test_dev_wallet_requires_dev_mode():
    s = settings()
    sk = SigningKey.generate()
    import hashlib
    address = "0:" + hashlib.sha256(bytes(sk.verify_key)).hexdigest()
    req = make_request(address, bytes(sk.verify_key), sk, None, s)
    with pytest.raises(TonProofError, match="state_init is required"):
        TonProofVerifier(s).verify(req)
    dev = settings(app_env="development", dev_wallet_login=True)
    req = make_request(address, bytes(sk.verify_key), sk, None, dev)
    assert TonProofVerifier(dev).verify(req).address == address
