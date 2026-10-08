"""Полное удаление пользователя из админ-панели.

Удаляется всё, что принадлежит пользователю: профиль, кошельки, устройства, сессии, ключи,
настройки, вложения, жалобы, которые он подал, его сообщения во всех чатах. Личные чаты с ним
и группы, которые он создал, удаляются целиком вместе с перепиской всех участников.
Из остальных групп он исключается так же, как при «Remove member»: новое событие состава
(от имени владельца группы) уходит в TON, после подтверждения участники поворачивают ключи.

Удаление идёт явными DELETE в порядке зависимостей, не полагаясь на ON DELETE CASCADE:
часть внешних ключей (messages.sender_id, threads.created_by, …) каскада не имеет.
Файлы на диске и доли ключей на realm-сервисах удаляет воркер — у админки к ним нет доступа.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import (
    Attachment,
    BlockedUser,
    ChainEvent,
    ConversationKey,
    Device,
    GroupInviteAllow,
    GroupKeyCommit,
    GroupMembershipEvent,
    GroupTreeNode,
    KeyBackup,
    KeyBackupShare,
    KeyRecoveryRequest,
    Message,
    MessageSearchToken,
    PushToken,
    RefreshToken,
    Report,
    Thread,
    ThreadMember,
    User,
    UserKeySet,
    UserSettings,
    UserWallet,
    new_uuid,
    utcnow,
)
from app.services import event_hash


@dataclass
class DeletionPlan:
    """Что затронет удаление — показывается на странице подтверждения."""

    direct_ids: list[str]
    own_group_ids: list[str]     # создал пользователь — удаляются целиком
    other_group_ids: list[str]   # пользователь участник — исключается
    messages: int                # сообщений, которые будут удалены (его и в удаляемых чатах)
    attachments: int
    devices: int


@dataclass
class DeletionResult:
    file_paths: list[str] = field(default_factory=list)
    membership_event_ids: list[str] = field(default_factory=list)
    deleted_thread_ids: list[str] = field(default_factory=list)
    left_group_ids: list[str] = field(default_factory=list)
    notify: dict[str, set[str]] = field(default_factory=dict)  # пользователь → чаты, которые у него изменились
    stats: dict[str, int] = field(default_factory=dict)


def _ids(s: Session, stmt) -> list[str]:
    return list(s.scalars(stmt))


def _count(s: Session, model, *where) -> int:
    return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


def plan(s: Session, user_id: str) -> DeletionPlan:
    member_threads = select(ThreadMember.thread_id).where(ThreadMember.user_id == user_id)
    direct_ids = _ids(s, select(Thread.id).where(
        Thread.kind == "direct", or_(Thread.id.in_(member_threads), Thread.created_by == user_id)))
    own_group_ids = _ids(s, select(Thread.id).where(Thread.kind == "group", Thread.created_by == user_id))
    other_group_ids = _ids(s, select(Thread.id).where(
        Thread.kind == "group", Thread.created_by != user_id, Thread.id.in_(member_threads)))
    doomed = direct_ids + own_group_ids
    return DeletionPlan(
        direct_ids=direct_ids, own_group_ids=own_group_ids, other_group_ids=other_group_ids,
        messages=_count(s, Message, or_(Message.sender_id == user_id, Message.thread_id.in_(doomed))),
        attachments=_count(s, Attachment, or_(Attachment.owner_id == user_id, Attachment.thread_id.in_(doomed)),
                           Attachment.deleted_at.is_(None)),
        devices=_count(s, Device, Device.user_id == user_id),
    )


def _active_members(s: Session, thread_id: str) -> list[ThreadMember]:
    return list(s.scalars(select(ThreadMember).where(ThreadMember.thread_id == thread_id,
                                                     ThreadMember.left_at.is_(None))
                          .order_by(ThreadMember.joined_at)))


def _leave_group(s: Session, user_id: str, thread_id: str, result: DeletionResult) -> bool:
    """Исключает пользователя из чужой группы. False — в группе больше никого нет, её нужно удалить."""
    members = _active_members(s, thread_id)
    target = next((m for m in members if m.user_id == user_id), None)
    rest = [m for m in members if m.user_id != user_id]
    if not rest:
        return False
    owner = next((m for m in rest if m.role == "owner"), None)
    if owner is None:  # владельцем был удаляемый — группа переходит к админу или самому давнему участнику
        owner = next((m for m in rest if m.role == "admin"), rest[0])
        owner.role = "owner"
    if target is not None:
        eid, now = new_uuid(), utcnow()
        s.add(GroupMembershipEvent(
            id=eid, thread_id=thread_id, actor_id=owner.user_id, action="remove", subject_user_id=user_id,
            leaf_index=target.leaf_index, created_at=now,
            event_hash=event_hash.membership_hash(eid, thread_id, owner.user_id, "remove", user_id, now),
        ))
        result.membership_event_ids.append(eid)
    # коммиты ключей, которые делал пользователь, нужны остальным для расшифровки истории
    s.execute(update(GroupKeyCommit).where(GroupKeyCommit.thread_id == thread_id,
                                           GroupKeyCommit.committer_id == user_id)
              .values(committer_id=owner.user_id))
    for m in rest:
        result.notify.setdefault(m.user_id, set()).add(thread_id)
    return True


def delete_user(s: Session, user_id: str) -> DeletionResult:
    """Удаляет пользователя в текущей транзакции; commit и задачи воркеру — на вызывающем."""
    p = plan(s, user_id)
    result = DeletionResult()

    doomed = p.direct_ids + p.own_group_ids
    for gid in p.other_group_ids:
        if _leave_group(s, user_id, gid, result):
            result.left_group_ids.append(gid)
        else:
            doomed.append(gid)
    for tid in doomed:
        for m in _active_members(s, tid):
            if m.user_id != user_id:
                result.notify.setdefault(m.user_id, set()).add(tid)
    result.deleted_thread_ids = doomed

    # вложения: файлы удалит воркер, записи — здесь
    attachments = or_(Attachment.owner_id == user_id, Attachment.thread_id.in_(doomed))
    result.file_paths = _ids(s, select(Attachment.storage_path).where(attachments, Attachment.deleted_at.is_(None)))

    # сообщения: его — во всех чатах, остальных — в удаляемых чатах
    doomed_messages = select(Message.id).where(or_(Message.sender_id == user_id, Message.thread_id.in_(doomed)))
    stats = result.stats
    stats["search_tokens"] = s.execute(delete(MessageSearchToken).where(
        or_(MessageSearchToken.message_id.in_(doomed_messages), MessageSearchToken.thread_id.in_(doomed)))).rowcount
    stats["messages"] = s.execute(delete(Message).where(
        or_(Message.sender_id == user_id, Message.thread_id.in_(doomed)))).rowcount
    stats["attachments"] = s.execute(delete(Attachment).where(attachments)).rowcount
    stats["chain_events"] = s.execute(delete(ChainEvent).where(
        or_(ChainEvent.actor_id == user_id, ChainEvent.thread_id.in_(doomed)))).rowcount

    # удаляемые чаты со всем содержимым
    for model in (ConversationKey, GroupKeyCommit, GroupTreeNode, GroupMembershipEvent, ThreadMember):
        s.execute(delete(model).where(model.thread_id.in_(doomed)))
    stats["threads"] = s.execute(delete(Thread).where(Thread.id.in_(doomed))).rowcount

    # следы пользователя в оставшихся чатах и у других пользователей
    s.execute(delete(GroupMembershipEvent).where(GroupMembershipEvent.actor_id == user_id))
    s.execute(delete(ConversationKey).where(or_(ConversationKey.recipient_id == user_id,
                                                ConversationKey.created_by == user_id)))
    s.execute(delete(ThreadMember).where(ThreadMember.user_id == user_id))
    s.execute(delete(BlockedUser).where(or_(BlockedUser.user_id == user_id, BlockedUser.blocked_user_id == user_id)))
    s.execute(delete(GroupInviteAllow).where(or_(GroupInviteAllow.user_id == user_id,
                                                 GroupInviteAllow.allowed_user_id == user_id)))
    s.execute(delete(Report).where(Report.reporter_id == user_id))

    # учётная запись
    for model in (PushToken, RefreshToken, Device, UserWallet, UserKeySet, KeyRecoveryRequest, KeyBackupShare,
                  KeyBackup, UserSettings):
        s.execute(delete(model).where(model.user_id == user_id))
    s.execute(delete(User).where(User.id == user_id))
    result.notify.pop(user_id, None)
    return result
