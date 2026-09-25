"""Серверная часть криптографического протокола (ТЗ 6).

* хранение и раздача публичных Identity/Signing-ключей (6.1);
* ретрансляция обёрнутых ECIES Conversation key личных чатов (6.3);
* TreeKEM-подобное дерево ключей групп: план затронутой ветки и приём коммитов (6.8);
* резервное копирование ключей по Шамиру 2-из-3 и восстановление через realm-сервисы (6.6).

Ни один метод не получает приватных ключей или открытых Conversation key.
"""
from __future__ import annotations

import base64
import binascii
import json

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ConversationKey,
    GroupKeyCommit,
    KeyBackup,
    KeyBackupShare,
    KeyRecoveryRequest,
    Thread,
    User,
    utcnow,
)
from app.repositories import KeyRepository, ThreadRepository
from app.schemas.keys import (
    BackupIn,
    BackupMetaOut,
    CommitNode,
    ConversationKeyOut,
    ConversationKeysIn,
    KeySetIn,
    KeySetOut,
    RealmOut,
    RecoveryIn,
    RecoveryOut,
    TreeCommitIn,
    TreeCommitOut,
    TreePlanOut,
)
from app.services import signatures, treekem
from app.services.errors import Conflict, DomainError, Forbidden, Invalid, NotFound
from app.services.infra import Infra
from app.services.thread_service import ThreadService

REALM_KEYS_REDIS = "realm:pubkeys"


class ServiceUnavailable(DomainError):
    status = 503
    code = "unavailable"


class KeyService:
    def __init__(self, session: AsyncSession, infra: Infra) -> None:
        self.s = session
        self.infra = infra
        self.keys = KeyRepository(session)
        self.threads = ThreadRepository(session)
        self.thread_svc = ThreadService(session, infra)

    # ------------------------------------------------------------------ публичные ключи (6.1)
    async def register(self, user: User, body: KeySetIn) -> tuple[KeySetOut, list[str]]:
        """Опубликовать новый набор ключей. Возвращает группы, где клиенту нужно обновить свою ветку."""
        for name, value in (("identity_pub", body.identity_pub), ("signing_pub", body.signing_pub)):
            if not signatures.is_valid_p256_spki(value):
                raise Invalid(f"{name} must be a P-256 SPKI public key")
        if body.identity_pub == body.signing_pub:
            raise Invalid("identity and signing keys must differ")
        ks = await self.keys.add_set(user.id, body.identity_pub, body.signing_pub)
        refresh_groups: list[str] = []
        if ks.version > 1:
            # сброс ключей: лист пользователя в каждой группе получает новый Identity-ключ,
            # а ветку до корня клиент перешифрует периодическим коммитом
            for thread, member in await self.threads.list_for_user(user.id):
                if thread.kind != "group" or member.leaf_index is None:
                    continue
                nodes = await self.threads.tree_nodes(thread.id)
                leaf = nodes.get(treekem.leaf_node(member.leaf_index))
                if leaf is not None and leaf.public_key:
                    leaf.public_key = body.identity_pub
                    refresh_groups.append(thread.id)
        await self.s.commit()
        return self._keyset_out(ks), refresh_groups

    @staticmethod
    def _keyset_out(ks) -> KeySetOut:
        return KeySetOut(user_id=ks.user_id, version=ks.version, identity_pub=ks.identity_pub,
                         signing_pub=ks.signing_pub, created_at=ks.created_at)

    async def get_keys(self, user_id: str, version: int | None = None) -> KeySetOut:
        ks = await (self.keys.set_version(user_id, version) if version else self.keys.active_set(user_id))
        if ks is None:
            raise NotFound("keys not found", code="no_keys")
        return self._keyset_out(ks)

    # ------------------------------------------------------------------ личные чаты (6.3)
    async def upload_conversation_keys(self, user: User, thread_id: str, body: ConversationKeysIn) -> int:
        thread, _ = await self.thread_svc.require_member(thread_id, user.id)
        if thread.kind != "direct":
            raise Invalid("group keys are managed by the key tree", code="use_tree")
        thread = await self.threads.get(thread_id, for_update=True)
        assert thread is not None
        if body.epoch != thread.key_epoch + 1:
            raise Conflict("epoch must be current + 1", code="stale_epoch", extra={"current_epoch": thread.key_epoch})
        member_ids = set(await self.threads.active_member_ids(thread_id))
        if {k.recipient_id for k in body.keys} != member_ids:
            raise Invalid("wrapped keys must be provided for every participant")
        active = await self.keys.active_sets(list(member_ids))
        for k in body.keys:
            if k.recipient_id not in active or active[k.recipient_id].version != k.recipient_key_version:
                raise Conflict("recipient key version is outdated", code="stale_recipient_key",
                               extra={"user_id": k.recipient_id})
            self.s.add(ConversationKey(thread_id=thread_id, epoch=body.epoch, recipient_id=k.recipient_id,
                                       recipient_key_version=k.recipient_key_version, wrapped_key=k.wrapped_key,
                                       created_by=user.id))
        thread.key_epoch = body.epoch
        try:
            await self.s.commit()
        except IntegrityError as exc:
            await self.s.rollback()
            raise Conflict("epoch already exists", code="stale_epoch") from exc
        for uid in member_ids:
            await self.infra.bus.publish(uid, "key.rotation",
                                         {"thread_id": thread_id, "phase": "applied", "epoch": body.epoch})
        return body.epoch

    async def conversation_keys(self, user: User, thread_id: str) -> list[ConversationKeyOut]:
        await self.thread_svc.require_member(thread_id, user.id)
        return [
            ConversationKeyOut(epoch=k.epoch, wrapped_key=k.wrapped_key, recipient_key_version=k.recipient_key_version,
                               created_by=k.created_by, created_at=k.created_at)
            for k in await self.keys.conversation_keys(thread_id, user.id)
        ]

    # ------------------------------------------------------------------ дерево группы (6.8)
    async def _state(self, thread: Thread) -> treekem.TreeState:
        nodes = await self.threads.tree_nodes(thread.id)
        return treekem.TreeState(capacity=thread.tree_leaf_capacity,
                                 pubs={i: n.public_key for i, n in nodes.items()})

    async def _plan(self, thread: Thread, user: User, want_periodic: bool) -> tuple[treekem.CommitPlan, str, list[str]]:
        requested = await self.threads.membership_events(thread.id, rotation_status="requested")
        state = await self._state(thread)
        if requested:
            full = any(e.action == "create" for e in requested)
            leaves = [e.leaf_index for e in requested if e.leaf_index is not None]
            reason = "create" if full else requested[0].action
            return treekem.plan_commit(state, leaves, full=full), reason, [e.id for e in requested]
        if not want_periodic:
            raise Conflict("no key rotation is pending", code="nothing_to_rotate")
        if thread.key_epoch == 0:
            raise Conflict("the group key tree is not initialized yet", code="tree_not_ready")
        member = await self.threads.membership(thread.id, user.id)
        if member is None or member.leaf_index is None:
            raise Forbidden("you have no leaf in this group")
        return treekem.plan_commit(state, [member.leaf_index]), "periodic", []

    async def _require_group(self, user: User, thread_id: str) -> Thread:
        thread, _ = await self.thread_svc.require_member(thread_id, user.id)
        if thread.kind != "group":
            raise Invalid("not a group")
        return thread

    async def tree_plan(self, user: User, thread_id: str, periodic: bool) -> TreePlanOut:
        thread = await self._require_group(user, thread_id)
        plan, reason, event_ids = await self._plan(thread, user, periodic)
        return TreePlanOut(
            thread_id=thread_id, next_epoch=thread.key_epoch + 1, capacity=plan.capacity, root=plan.root,
            path=plan.path, blanks=plan.blanks, targets=plan.targets, known_pubs=plan.known_pubs,
            reason=reason, membership_event_ids=event_ids,
        )

    async def tree_commit(self, user: User, thread_id: str, body: TreeCommitIn, periodic: bool) -> int:
        await self._require_group(user, thread_id)
        thread = await self.threads.get(thread_id, for_update=True)
        assert thread is not None
        if body.epoch != thread.key_epoch + 1:
            raise Conflict("epoch must be current + 1", code="stale_epoch", extra={"current_epoch": thread.key_epoch})
        plan, reason, event_ids = await self._plan(thread, user, periodic)
        nodes = [n.model_dump() for n in body.nodes]
        try:
            new_pubs = treekem.validate_commit(plan, nodes, body.root_ciphertext)
        except treekem.CommitValidationError as exc:
            raise Conflict(str(exc), code="plan_changed") from exc
        for idx, pub in new_pubs.items():
            if not signatures.is_valid_p256_spki(pub):
                raise Invalid(f"node {idx}: public key is not P-256")
            await self.threads.set_tree_node(thread_id, idx, pub, body.epoch)
        for idx in plan.blanks:
            await self.threads.set_tree_node(thread_id, idx, None, body.epoch)
        payload = {"nodes": nodes, "blanks": plan.blanks, "root": plan.root, "root_ciphertext": body.root_ciphertext}
        self.s.add(GroupKeyCommit(thread_id=thread_id, epoch=body.epoch, committer_id=user.id, reason=reason,
                                  membership_event_id=event_ids[0] if event_ids else None,
                                  payload_json=json.dumps(payload)))
        for ev in await self.threads.membership_events(thread_id, rotation_status="requested"):
            if ev.id in event_ids:
                ev.rotation_status = "done"
        thread.key_epoch = body.epoch
        thread.rotation_pending = False
        try:
            await self.s.commit()
        except IntegrityError as exc:
            await self.s.rollback()
            raise Conflict("another member already committed this epoch", code="stale_epoch") from exc
        for uid in await self.threads.active_member_ids(thread_id):
            await self.infra.bus.publish(uid, "key.rotation",
                                         {"thread_id": thread_id, "phase": "applied", "epoch": body.epoch})
        return body.epoch

    async def tree_commits(self, user: User, thread_id: str, since: int) -> list[TreeCommitOut]:
        await self._require_group(user, thread_id)
        member = await self.threads.membership(thread_id, user.id)
        out: list[TreeCommitOut] = []
        for c in await self.threads.commits_since(thread_id, since):
            p = json.loads(c.payload_json)
            out.append(TreeCommitOut(
                epoch=c.epoch, committer_id=c.committer_id, reason=c.reason,
                nodes=[CommitNode(**n) for n in p["nodes"]], blanks=p["blanks"], root=p["root"],
                root_ciphertext=p["root_ciphertext"], my_leaf=member.leaf_index if member else None,
                created_at=c.created_at,
            ))
        return out

    # ------------------------------------------------------------------ резервная копия (6.6)
    async def realms(self) -> list[RealmOut]:
        raw = await self.infra.redis.get(REALM_KEYS_REDIS)
        if not raw:
            self.infra.tasks.enqueue("keys.refresh_realm_keys")
            raise ServiceUnavailable("key recovery services are not reachable yet", code="realms_unavailable")
        return [RealmOut(**r) for r in json.loads(raw)]

    async def create_backup(self, user: User, body: BackupIn) -> BackupMetaOut:
        if await self.keys.active_set(user.id) is None:
            raise Conflict("publish your public keys first", code="no_keys")
        realms = await self.realms()
        indexes = sorted(s.realm_index for s in body.shares)
        if indexes != sorted(r.index for r in realms) or len(set(indexes)) != len(indexes):
            raise Invalid("exactly one sealed share per realm is required")
        if body.threshold > len(body.shares):
            raise Invalid("threshold exceeds share count")
        try:
            ciphertext = base64.b64decode(body.ciphertext, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise Invalid("ciphertext: invalid base64") from exc

        backup = await self.keys.backup(user.id)
        version = (backup.version + 1) if backup else 1
        if backup is None:
            backup = KeyBackup(user_id=user.id)
            self.s.add(backup)
        backup.version = version
        backup.ciphertext = ciphertext
        backup.kdf_salt = body.kdf_salt
        backup.kdf_iterations = body.kdf_iterations
        backup.threshold = body.threshold
        backup.share_count = len(body.shares)
        backup.status = "pending"
        existing = {sh.realm_index: sh for sh in await self.keys.backup_shares(user.id)}
        for share in body.shares:
            row = existing.get(share.realm_index)
            if row is None:
                row = KeyBackupShare(user_id=user.id, realm_index=share.realm_index, backup_version=version)
                self.s.add(row)
            row.backup_version = version
            row.status = "pending"
            row.last_error = None
        await self.s.commit()
        # доли запечатаны под ключи realm: брокер и воркер видят только шифротекст
        self.infra.tasks.enqueue("keys.store_shares", user.id, version,
                                 [{"realm_index": s.realm_index, "sealed": s.sealed} for s in body.shares])
        return await self.backup_meta(user, include_ciphertext=False)

    async def backup_meta(self, user: User, include_ciphertext: bool = True) -> BackupMetaOut:
        backup = await self.keys.backup(user.id)
        try:
            realms = await self.realms()
        except ServiceUnavailable:
            realms = []
        if backup is None:
            return BackupMetaOut(exists=False, realms=realms)
        return BackupMetaOut(
            exists=True, version=backup.version, kdf_salt=backup.kdf_salt, kdf_iterations=backup.kdf_iterations,
            threshold=backup.threshold, share_count=backup.share_count, status=backup.status,
            ciphertext=base64.b64encode(backup.ciphertext).decode() if include_ciphertext else None,
            realms=realms,
        )

    async def start_recovery(self, user: User, device_id: str, body: RecoveryIn) -> RecoveryOut:
        backup = await self.keys.backup(user.id)
        if backup is None or backup.status == "destroyed":
            raise NotFound("no key backup available", code="no_backup")
        if len(body.requests) < backup.threshold:
            raise Invalid("not enough realms requested")
        req = KeyRecoveryRequest(user_id=user.id, device_id=device_id)
        self.s.add(req)
        await self.s.commit()
        self.infra.tasks.enqueue("keys.recover", req.id,
                                 [{"realm_index": r.realm_index, "sealed_request": r.sealed_request} for r in body.requests])
        return RecoveryOut(id=req.id, status=req.status, attempts_left=None, shares=None)

    async def recovery_status(self, user: User, request_id: str) -> RecoveryOut:
        req = await self.keys.recovery(request_id)
        if req is None or req.user_id != user.id:
            raise NotFound("recovery request not found")
        shares = json.loads(req.result_json) if req.result_json and req.status == "done" else None
        return RecoveryOut(id=req.id, status=req.status, attempts_left=req.attempts_left, shares=shares)

    async def mark_backup_restored(self, user: User, request_id: str) -> None:
        """Клиент собрал ключи — результат (доли под эфемерный ключ) больше хранить незачем."""
        req = await self.keys.recovery(request_id)
        if req is not None and req.user_id == user.id:
            req.result_json = None
            req.finished_at = req.finished_at or utcnow()
            await self.s.commit()
