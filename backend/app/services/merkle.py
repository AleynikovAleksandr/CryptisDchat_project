"""Дерево Меркла для батч-записи подтверждений в TON (Gunicorn_Celery.md 4.2).

* лист     = SHA-256(0x00 || event_hash)
* узел     = SHA-256(0x01 || left || right)
* нечётный узел на уровне поднимается вверх без изменений (не дублируется),
  что исключает подделку дерева через повтор последнего листа.

Для каждого листа строится Merkle-proof — список соседних хэшей с их стороной.
Тот же алгоритм проверки реализован на клиенте (static/js/lib/crypto.js).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


def _leaf(event_hash_hex: str) -> bytes:
    return hashlib.sha256(b"\x00" + bytes.fromhex(event_hash_hex)).digest()


def _node(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


@dataclass(frozen=True)
class MerkleTree:
    root: str
    proofs: list[list[dict[str, str]]]  # proofs[i] — доказательство для листа i


def build(event_hashes: list[str]) -> MerkleTree:
    if not event_hashes:
        raise ValueError("empty batch")
    level = [_leaf(h) for h in event_hashes]
    # positions[i] — индекс узла, в котором сейчас находится лист i
    positions = list(range(len(level)))
    proofs: list[list[dict[str, str]]] = [[] for _ in event_hashes]

    while len(level) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_node(level[i], level[i + 1]))
            else:
                nxt.append(level[i])
        for leaf_idx, pos in enumerate(positions):
            if pos % 2 == 0 and pos + 1 < len(level):
                proofs[leaf_idx].append({"pos": "R", "hash": level[pos + 1].hex()})
            elif pos % 2 == 1:
                proofs[leaf_idx].append({"pos": "L", "hash": level[pos - 1].hex()})
            positions[leaf_idx] = pos // 2
        level = nxt

    return MerkleTree(root=level[0].hex(), proofs=proofs)


def verify(event_hash_hex: str, proof: list[dict[str, str]], root_hex: str) -> bool:
    acc = _leaf(event_hash_hex)
    for step in proof:
        sibling = bytes.fromhex(step["hash"])
        acc = _node(sibling, acc) if step["pos"] == "L" else _node(acc, sibling)
    return acc.hex() == root_hex
