"""Браузерные библиотеки (Node.js + WebCrypto) ↔ эталонная Python-реализация: форматы совпадают."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from nacl.secret import SecretBox

from app.proto import wire
from app.services import merkle, signatures, treekem
from realm import ecies
from tests import client_crypto as cc

SCRIPT = Path(__file__).parent / "js" / "interop.mjs"
b64 = cc.b64


@pytest.fixture(scope="module")
def run_node():
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    def _run(payload: dict) -> dict:
        proc = subprocess.run(["node", str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True,
                              timeout=60)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)
    return _run


def test_browser_crypto_matches_reference(run_node):
    ecdh = ec.generate_private_key(ec.SECP256R1())
    ecdsa = ec.generate_private_key(ec.SECP256R1())
    data = os.urandom(100)
    box_key, box_nonce = os.urandom(32), os.urandom(24)
    conv_key = os.urandom(32)
    secret = os.urandom(32)
    shares = cc.shamir_split(secret, 3, 2)
    hashes = [hashlib.sha256(bytes([i])).hexdigest() for i in range(5)]
    tree = merkle.build(hashes)
    frame = wire.encode("WsFrame", {"event": "message.new", "seq": 2**40 + 5, "ts": 1_758_000_000_123, "packet": {
        "nonce": box_nonce, "search_tokens": ["x" * 32], "push_preview": "Привет"}})

    # TreeKEM: дерево на 3 участника, коммит строит JS, ещё один — Python
    members = [ec.generate_private_key(ec.SECP256R1()) for _ in range(3)]
    state = treekem.TreeState(4, {treekem.leaf_node(i): cc.spki(m.public_key()) for i, m in enumerate(members)})
    plan = treekem.plan_commit(state, [], full=True)
    plan_json = {"path": plan.path, "targets": {str(k): v for k, v in plan.targets.items()},
                 "known_pubs": {str(k): v for k, v in plan.known_pubs.items()}, "root": plan.root, "next_epoch": 1}
    py_body, py_secret = cc.build_commit(plan_json)
    py_commit = {**py_body, "blanks": plan.blanks, "root": plan.root, "my_leaf": 1}

    sb = {"thread_id": "t-1", "client_msg_id": "c-1", "epoch": 3, "nonce": b64(box_nonce), "ct": b64(data)}
    out = run_node({
        "ecdh": {"priv_pkcs8": b64(cc.pkcs8(ecdh)), "pub": cc.spki(ecdh.public_key()),
                 "sealed": ecies.seal(cc.spki(ecdh.public_key()), b"py-secret")},
        "ecdsa": {"pub": cc.spki(ecdsa.public_key()), "data": b64(data), "sig": b64(cc.sign_p1363(ecdsa, data))},
        "signing_bytes": sb,
        "box": {"key": b64(box_key), "nonce": b64(box_nonce),
                "ct": b64(SecretBox(box_key).encrypt(b"hello box", box_nonce).ciphertext)},
        "search": {"key": b64(conv_key), "text": "Deploy the new Build, ёжик!", "word": "Build"},
        "shamir": {"shares": [b64(s) for s in shares], "secret": b64(secret)},
        "merkle": {"hash": hashes[3], "proof": tree.proofs[3], "root": tree.root},
        "proto": {"frame": b64(frame), "nonce": b64(box_nonce)},
        "tree": {"plan": plan_json, "py_commit": py_commit, "member_pkcs8": b64(cc.pkcs8(members[1]))},
        "markdown": [
            "<img src=x onerror=alert(1)>",
            "[click](javascript:alert(1))",
            "**bold** *it* ~~del~~ `a<b>` [ok](https://ok.io)",
            "```js\nconst x = \"<s>\"; // hi\n```",
            "> quote\n- a\n- b\n1. one",
            'https://evil.io/"onmouseover=alert(1)',
        ],
    })

    # ECIES в обе стороны
    assert out["opened_py"] == "py-secret"
    assert ecies.open_sealed(ecdh, out["sealed_by_js"]) == b"js-secret"
    # ECDSA r||s в обе стороны; подписываемые байты идентичны серверным
    assert out["sig_ok"] is True and out["sig_bad"] is False
    assert signatures.verify_p1363(out["js_sig"]["pub"], base64.b64decode(out["js_sig"]["sig"]), data)
    assert base64.b64decode(out["signing_bytes"]) == signatures.signing_bytes("t-1", "c-1", 3, box_nonce, data)
    # XSalsa20-Poly1305
    assert out["box_open"] == "hello box"
    assert SecretBox(box_key).decrypt(base64.b64decode(out["js_box"]["ct"]), base64.b64decode(out["js_box"]["nonce"])) == b"from js"
    # слепой индекс: клиент и эталон считают одинаковые токены
    assert out["tokens"] == cc.word_tokens(conv_key, "Deploy the new Build, ёжик!")
    assert out["token_word"] == cc.search_token(conv_key, "build")
    # Шамир в обе стороны
    assert base64.b64decode(out["combined"]) == secret
    js_shares = [base64.b64decode(s) for s in out["js_shares"]]
    assert cc.shamir_combine([js_shares[0], js_shares[2]]) == secret
    # Меркл
    assert out["merkle_ok"] is True and out["merkle_bad"] is False
    # protobuf
    assert out["proto_decoded"] == {"event": "message.new", "seq": 2**40 + 5, "ts": 1_758_000_000_123,
                                    "nonce": b64(box_nonce), "tokens": ["x" * 32], "preview": "Привет"}
    decoded = wire.decode("MessagePacket", base64.b64decode(out["proto_js"]))
    assert decoded["key_epoch"] == 7 and decoded["nonce"] == box_nonce and decoded["push_preview"] == "Ёж"

    # TreeKEM: коммит из браузера принимается сервером и все участники Python получают тот же ключ
    assert treekem.validate_commit(plan, out["tree_commit"]["nodes"], out["tree_commit"]["root_ciphertext"])
    js_commit = {**out["tree_commit"], "blanks": plan.blanks, "root": plan.root}
    for i, m in enumerate(members):
        member = cc.TreeMember(identity=m)
        member.set_leaf(i)
        assert cc.process_commit(member, js_commit) == base64.b64decode(out["tree_secret"])
    assert base64.b64decode(out["tree_processed"]) == py_secret

    # Markdown: никакого исполняемого HTML
    xss_img, xss_link, fmt, code, blocks, xss_attr = out["md"]
    assert "<img" not in xss_img and "&lt;img" in xss_img
    assert "href" not in xss_link
    assert "<strong>bold</strong>" in fmt and "<em>it</em>" in fmt and "<del>del</del>" in fmt
    assert "<code>a&lt;b&gt;</code>" in fmt and 'href="https://ok.io"' in fmt
    assert '<span class="tok-kw">const</span>' in code and "&lt;s&gt;" in code and "tok-com" in code
    assert "<blockquote>" in blocks and "<ul><li>a</li><li>b</li></ul>" in blocks and "<ol><li>one</li></ol>" in blocks
    assert '"onmouseover' not in xss_attr and "onmouseover=" not in xss_attr.split("href=")[1].split(">")[0]
    assert out["md_plain"] == "Hi there code link quote"
