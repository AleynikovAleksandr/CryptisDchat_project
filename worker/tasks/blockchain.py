"""Очередь `blockchain` (Gunicorn_Celery.md 4.2–4.4, 4.8).

flush_batch        — забрать накопленные хэши, построить дерево Меркла, создать батч;
submit_batch       — отправить ОДНУ транзакцию с корнем от служебного кошелька (идемпотентно);
confirm_batch      — дождаться подтверждения, проставить статусы, уведомить клиентов;
anchor_membership  — транзакция изменения состава группы;
confirm_membership — после подтверждения: применить изменение листа в дереве ключей и
                     запросить у клиентов перешифровку затронутой ветки (key.rotation).
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.models import (
    ChainBatch,
    ChainEvent,
    GroupMembershipEvent,
    GroupTreeNode,
    Thread,
    ThreadMember,
    UserKeySet,
    utcnow,
)
from app.services import merkle, treekem
from app.services.cache import CHAIN_PENDING_KEY
from worker.celery_app import celery_app, retry_delay
from worker.runtime import blockchain, cache_redis, db, publish_many

log = logging.getLogger(__name__)

MAX_RETRIES = 5
CONFIRM_POLL_SECONDS = 10
CONFIRM_MAX_POLLS = 90          # ~15 минут, дальше подхватит recheck_submitted
FLUSH_LOCK = "lock:chain:flush"


def batch_comment(root: str) -> str:
    return f"cryptis:v1:batch:{root}"


def membership_comment(thread_id: str, event_hash: str) -> str:
    return f"cryptis:v1:group:{thread_id}:{event_hash}"


# ---------------------------------------------------------------------------- батчи сообщений
@celery_app.task(name="blockchain.flush_batch")
def flush_batch() -> str | None:
    r = cache_redis()
    if not r.set(FLUSH_LOCK, "1", nx=True, ex=300):
        return None  # батч уже собирает другой процесс
    try:
        limit = get_settings().chain_batch_max_items
        ids = r.lpop(CHAIN_PENDING_KEY, limit) or []
        with db() as session:
            events = []
            if ids:
                events = list(session.scalars(
                    select(ChainEvent).where(ChainEvent.id.in_(ids), ChainEvent.status == "queued")
                    .order_by(ChainEvent.created_at)
                ))
            if not events:
                return None
            tree = merkle.build([e.event_hash for e in events])
            batch = ChainBatch(merkle_root=tree.root, leaf_count=len(events), status="pending")
            session.add(batch)
            session.flush()
            for i, (ev, proof) in enumerate(zip(events, tree.proofs)):
                ev.batch_id = batch.id
                ev.leaf_index = i
                ev.merkle_proof = json.dumps(proof)
                ev.status = "batched"
            session.commit()
            batch_id = batch.id
        log.info("batch %s: %d events, root %s", batch_id, len(events), tree.root)
        submit_batch.delay(batch_id)
        return batch_id
    finally:
        r.delete(FLUSH_LOCK)


@celery_app.task(name="blockchain.sweep_orphans")
def sweep_orphans() -> int:
    """События, id которых потерялись между Redis и БД (падение процесса), возвращаются в очередь."""
    cutoff = utcnow() - timedelta(minutes=10)
    with db() as session:
        ids = list(session.scalars(
            select(ChainEvent.id).where(ChainEvent.status == "queued", ChainEvent.created_at < cutoff).limit(5000)
        ))
    if ids:
        cache_redis().rpush(CHAIN_PENDING_KEY, *ids)
    return len(ids)


@celery_app.task(name="blockchain.submit_batch", bind=True, acks_late=True, max_retries=MAX_RETRIES)
def submit_batch(self, batch_id: str) -> None:
    with db() as session:
        batch = session.get(ChainBatch, batch_id)
        if batch is None or batch.status in ("submitted", "confirmed"):
            return  # идемпотентность: повтор не отправит вторую транзакцию
        try:
            result = blockchain().anchor(batch_comment(batch.merkle_root), idempotency_key=batch.id)
        except Exception as exc:  # noqa: BLE001 — нода недоступна, нет баланса, перегрузка сети
            batch.attempts += 1
            batch.last_error = str(exc)[:2000]
            if self.request.retries >= MAX_RETRIES:
                batch.status = "failed"  # виден в /admin/blockchain-queue, повтор — вручную
                session.commit()
                log.error("batch %s failed permanently: %s", batch_id, exc)
                return
            session.commit()
            raise self.retry(exc=exc, countdown=retry_delay(self.request.retries)) from exc
        batch.status = "submitted"
        batch.tx_hash = result.tx_ref
        batch.submitted_at = utcnow()
        batch.attempts += 1
        batch.last_error = None
        session.commit()
    confirm_batch.apply_async(args=[batch_id], countdown=CONFIRM_POLL_SECONDS)


@celery_app.task(name="blockchain.confirm_batch", bind=True, acks_late=True, max_retries=CONFIRM_MAX_POLLS)
def confirm_batch(self, batch_id: str) -> bool:
    with db() as session:
        batch = session.get(ChainBatch, batch_id)
        if batch is None or batch.status != "submitted" or not batch.tx_hash:
            return batch is not None and batch.status == "confirmed"
        result = blockchain().check(batch.tx_hash)
        if not result.confirmed:
            if self.request.retries < CONFIRM_MAX_POLLS:
                raise self.retry(countdown=CONFIRM_POLL_SECONDS)
            return False
        batch.status = "confirmed"
        batch.tx_hash = result.tx_hash or batch.tx_hash
        batch.tx_lt = result.lt
        batch.confirmed_at = utcnow()
        events = list(session.scalars(select(ChainEvent).where(ChainEvent.batch_id == batch_id)))
        for ev in events:
            ev.status = "confirmed"
        session.commit()
        _notify_confirmed(session, batch, events)
    return True


def _notify_confirmed(session, batch: ChainBatch, events: list[ChainEvent]) -> None:
    """4.3: клиенты с открытым диалогом видят иконку подтверждения без перезагрузки."""
    by_thread: dict[str, list[str]] = {}
    for ev in events:
        if ev.event_type == "message.sent" and ev.message_id:
            by_thread.setdefault(ev.thread_id, []).append(ev.message_id)
    for thread_id, message_ids in by_thread.items():
        members = list(session.scalars(
            select(ThreadMember.user_id).where(ThreadMember.thread_id == thread_id, ThreadMember.left_at.is_(None))
        ))
        publish_many(members, "message.confirmed", {
            "thread_id": thread_id, "message_ids": message_ids,
            "batch_id": batch.id, "tx_hash": batch.tx_hash, "merkle_root": batch.merkle_root,
        })


@celery_app.task(name="blockchain.recheck_submitted")
def recheck_submitted() -> int:
    with db() as session:
        ids = list(session.scalars(select(ChainBatch.id).where(ChainBatch.status == "submitted").limit(200)))
        pending = list(session.scalars(select(ChainBatch.id).where(ChainBatch.status == "pending",
                                                                   ChainBatch.created_at < utcnow() - timedelta(minutes=5))))
        events = list(session.scalars(
            select(GroupMembershipEvent.id).where(GroupMembershipEvent.chain_status == "submitted").limit(200)
        ))
    for batch_id in ids:
        confirm_batch.delay(batch_id)
    for batch_id in pending:
        submit_batch.delay(batch_id)
    for event_id in events:
        confirm_membership.delay(event_id)
    return len(ids) + len(pending) + len(events)


@celery_app.task(name="blockchain.retry_batch")
def retry_batch(batch_id: str) -> None:
    """Ручной повтор из админ-панели (/admin/blockchain-queue/retry/<batch_id>)."""
    with db() as session:
        batch = session.get(ChainBatch, batch_id)
        if batch is None or batch.status != "failed":
            return
        batch.status = "pending"
        session.commit()
    submit_batch.delay(batch_id)


# ---------------------------------------------------------------------------- состав групп
@celery_app.task(name="blockchain.anchor_membership", bind=True, acks_late=True, max_retries=MAX_RETRIES)
def anchor_membership(self, event_id: str) -> None:
    with db() as session:
        ev = session.get(GroupMembershipEvent, event_id)
        if ev is None or ev.chain_status in ("submitted", "confirmed"):
            return
        try:
            result = blockchain().anchor(membership_comment(ev.thread_id, ev.event_hash), idempotency_key=ev.id)
        except Exception as exc:  # noqa: BLE001
            ev.attempts += 1
            ev.last_error = str(exc)[:2000]
            if self.request.retries >= MAX_RETRIES:
                ev.chain_status = "failed"
                session.commit()
                return
            session.commit()
            raise self.retry(exc=exc, countdown=retry_delay(self.request.retries)) from exc
        ev.chain_status = "submitted"
        ev.tx_hash = result.tx_ref
        ev.attempts += 1
        session.commit()
    confirm_membership.apply_async(args=[event_id], countdown=CONFIRM_POLL_SECONDS)


@celery_app.task(name="blockchain.confirm_membership", bind=True, acks_late=True, max_retries=CONFIRM_MAX_POLLS)
def confirm_membership(self, event_id: str) -> bool:
    with db() as session:
        ev = session.get(GroupMembershipEvent, event_id)
        if ev is None or ev.chain_status != "submitted" or not ev.tx_hash:
            return ev is not None and ev.chain_status == "confirmed"
        result = blockchain().check(ev.tx_hash)
        if not result.confirmed:
            if self.request.retries < CONFIRM_MAX_POLLS:
                raise self.retry(countdown=CONFIRM_POLL_SECONDS)
            return False
        ev.chain_status = "confirmed"
        ev.tx_hash = result.tx_hash or ev.tx_hash
        ev.confirmed_at = utcnow()
        # 4.4: порядок важен — сначала подтверждённая запись в блокчейн, потом ротация ключей
        apply_membership_to_tree(session, ev)
        session.commit()
        members = list(session.scalars(
            select(ThreadMember.user_id).where(ThreadMember.thread_id == ev.thread_id, ThreadMember.left_at.is_(None))
        ))
        thread_id, action, actor_id = ev.thread_id, ev.action, ev.actor_id
    # коммит делает инициатор изменения; остальные участники — резерв, если он офлайн
    publish_many(members, "key.rotation",
                 {"thread_id": thread_id, "phase": "requested", "reason": action, "actor_id": actor_id})
    return True


def apply_membership_to_tree(session, ev: GroupMembershipEvent) -> None:
    thread = session.get(Thread, ev.thread_id)
    if thread is None:
        return

    def identity(user_id: str) -> str | None:
        ks = session.scalar(select(UserKeySet).where(UserKeySet.user_id == user_id, UserKeySet.is_active.is_(True)))
        return ks.identity_pub if ks else None

    def set_leaf(leaf: int, pub: str | None, owner: str | None) -> None:
        idx = treekem.leaf_node(leaf)
        node = session.get(GroupTreeNode, (thread.id, idx))
        if node is None:
            session.add(GroupTreeNode(thread_id=thread.id, node_index=idx, public_key=pub, owner_user_id=owner,
                                      epoch=thread.key_epoch))
        else:
            node.public_key, node.owner_user_id, node.epoch = pub, owner, thread.key_epoch

    if ev.action == "create":
        for m in session.scalars(select(ThreadMember).where(ThreadMember.thread_id == thread.id,
                                                             ThreadMember.left_at.is_(None))):
            if m.leaf_index is not None:
                set_leaf(m.leaf_index, identity(m.user_id), m.user_id)
    elif ev.action == "add" and ev.leaf_index is not None and ev.subject_user_id:
        m = session.get(ThreadMember, (thread.id, ev.subject_user_id))
        if m is not None and m.left_at is None and m.leaf_index == ev.leaf_index:
            set_leaf(ev.leaf_index, identity(ev.subject_user_id), ev.subject_user_id)
    elif ev.action in ("remove", "leave") and ev.leaf_index is not None:
        set_leaf(ev.leaf_index, None, None)

    ev.rotation_status = "requested"
    thread.rotation_pending = True
