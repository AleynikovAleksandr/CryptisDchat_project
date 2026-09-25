"""Модульные тесты чистой логики: Меркл, TreeKEM-арифметика, JWT, payload ton_proof."""
from __future__ import annotations

import hashlib
import time

import pytest

from app.services import merkle, security, treekem
from app.services.ton_proof import TonProofError, check_payload, make_payload, to_friendly


@pytest.mark.parametrize("n", [1, 2, 3, 5, 8, 13, 100])
def test_merkle_every_leaf_verifies(n):
    hashes = [hashlib.sha256(f"ev{i}".encode()).hexdigest() for i in range(n)]
    tree = merkle.build(hashes)
    for h, proof in zip(hashes, tree.proofs):
        assert merkle.verify(h, proof, tree.root)


def test_merkle_rejects_wrong_leaf_and_tampered_proof():
    hashes = [hashlib.sha256(bytes([i])).hexdigest() for i in range(6)]
    tree = merkle.build(hashes)
    assert not merkle.verify(hashes[0], tree.proofs[1], tree.root)
    bad = [dict(step) for step in tree.proofs[2]]
    bad[0]["hash"] = "00" * 32
    assert not merkle.verify(hashes[2], bad, tree.root)


def test_merkle_odd_leaf_is_not_duplicated():
    # при дублировании последнего листа деревья [a,b,c] и [a,b,c,c] совпали бы по корню
    a, b, c = (hashlib.sha256(x).hexdigest() for x in (b"a", b"b", b"c"))
    assert merkle.build([a, b, c]).root != merkle.build([a, b, c, c]).root


def test_treekem_arithmetic_matches_rfc9420_layout():
    assert [treekem.level(x) for x in range(7)] == [0, 1, 0, 2, 0, 1, 0]
    assert treekem.root(4) == 3 and treekem.root(8) == 7
    assert treekem.left(3) == 1 and treekem.right(3) == 5
    assert treekem.parent(0, 4) == 1 and treekem.parent(5, 4) == 3
    assert treekem.direct_path(treekem.leaf_node(2), 4) == [5, 3]
    assert list(treekem.subtree_leaves(5)) == [2, 3]
    assert treekem.capacity_for(5) == 8


def test_treekem_remove_touches_only_the_branch():
    cap = 8
    pubs = {treekem.leaf_node(i): f"P{i}".ljust(80, "x") for i in range(8)}
    pubs.update({n: f"N{n}".ljust(80, "x") for n in range(15) if treekem.level(n) > 0})
    pubs[treekem.leaf_node(5)] = None  # лист 5 удалён
    plan = treekem.plan_commit(treekem.TreeState(cap, pubs), [5])
    assert plan.path == [9, 11, 7]                  # log2(8) = 3 узла, а не все 7 внутренних
    assert plan.targets[9] == [8]                   # удалённый лист 10 не получает ключ
    assert plan.targets[11] == [9, 13]
    assert plan.targets[7] == [3, 11]
    assert sum(len(t) for t in plan.targets.values()) == 5  # O(log N) шифрований


def test_treekem_blanks_empty_subtrees():
    cap = 4
    pubs = {0: "A" * 80, 2: "B" * 80, 1: "N" * 80, 3: "R" * 80, 4: None, 6: None, 5: "Z" * 80}
    plan = treekem.plan_commit(treekem.TreeState(cap, pubs), [2])
    assert 5 in plan.blanks and 5 not in plan.path


def test_treekem_validate_commit_rejects_wrong_targets():
    plan = treekem.plan_commit(treekem.TreeState(2, {0: "A" * 80, 2: "B" * 80}), [], full=True)
    good = [{"index": 1, "public_key": "K" * 90, "ciphertexts": [{"target": 0, "ct": "c"}, {"target": 2, "ct": "c"}]}]
    assert treekem.validate_commit(plan, good, "r" * 60) == {1: "K" * 90}
    bad = [{"index": 1, "public_key": "K" * 90, "ciphertexts": [{"target": 0, "ct": "c"}]}]
    with pytest.raises(treekem.CommitValidationError):
        treekem.validate_commit(plan, bad, "r" * 60)


def test_jwt_roundtrip_and_revocation_boundary():
    token, _ = security.create_access_token("u1", "d1")
    claims = security.decode_access_token(token)
    assert claims.user_id == "u1" and claims.device_id == "d1"
    from datetime import datetime, timezone
    before = datetime.fromtimestamp(claims.issued_at - 5, tz=timezone.utc).replace(tzinfo=None)
    after = datetime.fromtimestamp(claims.issued_at + 1, tz=timezone.utc).replace(tzinfo=None)
    assert not security.issued_before(claims, before)
    assert security.issued_before(claims, after)
    with pytest.raises(security.TokenError):
        security.decode_access_token(token + "x")


def test_ton_proof_payload_is_signed_and_expires():
    p = make_payload("s", 60)
    check_payload("s", p)
    with pytest.raises(TonProofError):
        check_payload("other-secret", p)
    expired = make_payload("s", -1)
    with pytest.raises(TonProofError):
        check_payload("s", expired)
    assert time.time() > 0


def test_friendly_address_format():
    raw = "0:" + "ab" * 32
    friendly = to_friendly(raw)
    assert len(friendly) == 48 and friendly.startswith("UQ")
