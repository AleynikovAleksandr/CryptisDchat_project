"""TreeKEM-подобное дерево ключей группы (ТЗ 6.8) — серверная «бухгалтерия».

Сервер никогда не видит приватных ключей узлов. Его задача — знать форму дерева
и публичные ключи узлов, и по изменению состава вычислить *затронутую ветку*:

* листья — участники группы (публичный ключ листа = Identity-ключ участника);
* у каждого внутреннего узла есть пара ключей ECDH P-256, приватную часть знают
  ровно те участники, что находятся под этим узлом;
* при добавлении/удалении участника обновляется только путь от его листа до корня:
  клиент-коммитер генерирует новые пары ключей для узлов пути и шифрует (ECIES)
  приватный ключ каждого узла под публичные ключи «резолюции» его детей;
* групповой Conversation key эпохи шифруется под новый ключ корня.

Итого на изменение состава — O(log N) шифрований, а не рассылка ключа всем N.

Нумерация узлов — «массивное» представление левосбалансированного дерева (как в MLS,
RFC 9420 Appendix C): лист i — узел 2i, уровень узла — число младших единичных битов.
Ёмкость дерева (число листьев) — степень двойки; при нехватке места удваивается.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# --------------------------------------------------------------------------- арифметика
def level(x: int) -> int:
    k = 0
    while (x >> k) & 1:
        k += 1
    return k


def leaf_node(leaf: int) -> int:
    return 2 * leaf


def node_count(capacity: int) -> int:
    return 2 * capacity - 1


def root(capacity: int) -> int:
    return capacity - 1


def left(x: int) -> int:
    k = level(x)
    if k == 0:
        raise ValueError("leaf has no children")
    return x ^ (1 << (k - 1))


def right(x: int) -> int:
    k = level(x)
    if k == 0:
        raise ValueError("leaf has no children")
    return x ^ (3 << (k - 1))


def parent(x: int, capacity: int) -> int:
    if x == root(capacity):
        raise ValueError("root has no parent")
    k = level(x)
    b = (x >> (k + 1)) & 1
    return (x | (1 << k)) ^ (b << (k + 1))


def direct_path(x: int, capacity: int) -> list[int]:
    """Узлы от родителя x до корня включительно (снизу вверх)."""
    path: list[int] = []
    r = root(capacity)
    while x != r:
        x = parent(x, capacity)
        path.append(x)
    return path


def subtree_leaves(x: int) -> range:
    """Номера листьев (не узлов) в поддереве узла x."""
    k = level(x)
    first_node = x - ((1 << k) - 1)
    return range(first_node // 2, first_node // 2 + (1 << k))


def capacity_for(leaf_count: int) -> int:
    cap = 1
    while cap < leaf_count:
        cap *= 2
    return cap


# --------------------------------------------------------------------------- планирование
@dataclass
class TreeState:
    """Состояние дерева: публичные ключи узлов (None/отсутствие = пустой узел)."""

    capacity: int
    pubs: dict[int, str | None] = field(default_factory=dict)

    def pub(self, node: int) -> str | None:
        return self.pubs.get(node)

    def occupied_leaves(self) -> set[int]:
        return {n // 2 for n, p in self.pubs.items() if n % 2 == 0 and p}


@dataclass
class CommitPlan:
    """Что должен сделать клиент-коммитер.

    path    — внутренние узлы, получающие новые пары ключей (снизу вверх);
    blanks  — узлы пути, которые становятся пустыми (под ними никого нет);
    targets — для каждого узла пути: индексы узлов, под чьи публичные ключи
              шифруется его приватный ключ;
    known_pubs — публичные ключи целевых узлов, не входящих в path.
    """

    capacity: int
    root: int
    path: list[int]
    blanks: list[int]
    targets: dict[int, list[int]]
    known_pubs: dict[int, str]


def resolution(node: int, state: TreeState, fresh: set[int]) -> list[int]:
    """Минимальный набор непустых узлов, покрывающий всех участников поддерева."""
    if node in fresh or state.pub(node):
        return [node]
    if level(node) == 0:
        return []
    return resolution(left(node), state, fresh) + resolution(right(node), state, fresh)


def plan_commit(state: TreeState, target_leaves: list[int], full: bool = False) -> CommitPlan:
    """Спланировать обновление путей от `target_leaves` (или всего дерева, если full).

    `state` уже должен отражать изменение листьев (новый лист занят, удалённый пуст).
    """
    cap = state.capacity
    occupied = state.occupied_leaves()
    if not occupied:
        raise ValueError("group has no members")

    if full:
        candidates = {n for n in range(node_count(cap)) if level(n) > 0}
    else:
        candidates = set()
        for leaf in target_leaves:
            candidates.update(direct_path(leaf_node(leaf), cap))
    if cap == 1:
        # единственный лист — он же корень; обновлять нечего, эпоху шифруем под лист
        candidates = set()

    refreshed: set[int] = set()
    blanks: set[int] = set()
    for n in candidates:
        if any(leaf in occupied for leaf in subtree_leaves(n)):
            refreshed.add(n)
        else:
            blanks.add(n)

    order = sorted(refreshed, key=lambda n: (level(n), n))
    targets: dict[int, list[int]] = {}
    known: dict[int, str] = {}
    for n in order:
        t = resolution(left(n), state, refreshed) + resolution(right(n), state, refreshed)
        targets[n] = t
        for x in t:
            if x not in refreshed:
                pub = state.pub(x)
                assert pub is not None
                known[x] = pub
    r = root(cap)
    if r not in refreshed and state.pub(r):
        known[r] = state.pub(r)  # type: ignore[assignment]
    return CommitPlan(capacity=cap, root=r, path=order, blanks=sorted(blanks), targets=targets, known_pubs=known)


class CommitValidationError(ValueError):
    pass


def validate_commit(plan: CommitPlan, nodes: list[dict], root_ciphertext: str) -> dict[int, str]:
    """Проверить, что коммит клиента в точности соответствует плану.

    `nodes` — [{"index": int, "public_key": str, "ciphertexts": [{"target": int, "ct": str}]}].
    Возвращает {индекс узла: новый публичный ключ}.
    """
    by_index = {int(n["index"]): n for n in nodes}
    if set(by_index) != set(plan.path):
        raise CommitValidationError("commit nodes do not match the affected branch")
    new_pubs: dict[int, str] = {}
    for idx in plan.path:
        node = by_index[idx]
        pub = node.get("public_key")
        if not isinstance(pub, str) or not 60 <= len(pub) <= 200:
            raise CommitValidationError(f"node {idx}: bad public key")
        got = sorted(int(c["target"]) for c in node.get("ciphertexts", []))
        if got != sorted(plan.targets[idx]):
            raise CommitValidationError(f"node {idx}: ciphertext targets mismatch")
        for c in node["ciphertexts"]:
            if not isinstance(c.get("ct"), str) or len(c["ct"]) > 4096:
                raise CommitValidationError(f"node {idx}: bad ciphertext")
        new_pubs[idx] = pub
    if not isinstance(root_ciphertext, str) or not 40 < len(root_ciphertext) < 4096:
        raise CommitValidationError("bad root ciphertext")
    return new_pubs
