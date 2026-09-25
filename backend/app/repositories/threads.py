from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    GroupKeyCommit,
    GroupMembershipEvent,
    GroupTreeNode,
    Message,
    Thread,
    ThreadMember,
)


class ThreadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, thread_id: str, *, for_update: bool = False) -> Thread | None:
        if for_update:
            return await self.s.scalar(select(Thread).where(Thread.id == thread_id).with_for_update())
        return await self.s.get(Thread, thread_id)

    async def by_direct_key(self, key: str) -> Thread | None:
        return await self.s.scalar(select(Thread).where(Thread.direct_key == key))

    async def membership(self, thread_id: str, user_id: str) -> ThreadMember | None:
        return await self.s.get(ThreadMember, (thread_id, user_id))

    async def active_members(self, thread_id: str) -> list[ThreadMember]:
        rows = await self.s.scalars(
            select(ThreadMember).where(ThreadMember.thread_id == thread_id, ThreadMember.left_at.is_(None))
            .order_by(ThreadMember.joined_at)
        )
        return list(rows)

    async def active_member_ids(self, thread_id: str) -> list[str]:
        return [m.user_id for m in await self.active_members(thread_id)]

    async def members_for_threads(self, thread_ids: list[str]) -> dict[str, list[ThreadMember]]:
        out: dict[str, list[ThreadMember]] = {t: [] for t in thread_ids}
        if not thread_ids:
            return out
        rows = await self.s.scalars(
            select(ThreadMember).where(ThreadMember.thread_id.in_(thread_ids), ThreadMember.left_at.is_(None))
        )
        for m in rows:
            out[m.thread_id].append(m)
        return out

    async def list_for_user(self, user_id: str) -> list[tuple[Thread, ThreadMember]]:
        rows = await self.s.execute(
            select(Thread, ThreadMember)
            .join(ThreadMember, ThreadMember.thread_id == Thread.id)
            .where(ThreadMember.user_id == user_id, ThreadMember.left_at.is_(None))
            .order_by(func.coalesce(Thread.last_message_at, Thread.created_at).desc())
        )
        return [(t, m) for t, m in rows.all()]

    async def unread_counts(self, user_id: str, pairs: list[tuple[str, int, int]]) -> dict[str, int]:
        """pairs: (thread_id, last_read_seq, cleared_seq) → число чужих непрочитанных сообщений."""
        out: dict[str, int] = {}
        for thread_id, last_read, cleared in pairs:
            floor = max(last_read, cleared)
            count = await self.s.scalar(
                select(func.count()).select_from(Message).where(
                    Message.thread_id == thread_id, Message.seq > floor, Message.sender_id != user_id
                )
            )
            out[thread_id] = int(count or 0)
        return out

    async def next_seq(self, thread: Thread) -> int:
        """Атомарно выделить следующий номер сообщения (строка треда заблокирована FOR UPDATE)."""
        await self.s.execute(update(Thread).where(Thread.id == thread.id).values(last_seq=Thread.last_seq + 1))
        seq = await self.s.scalar(select(Thread.last_seq).where(Thread.id == thread.id))
        return int(seq)

    # --- дерево ключей группы ---
    async def tree_nodes(self, thread_id: str) -> dict[int, GroupTreeNode]:
        rows = await self.s.scalars(select(GroupTreeNode).where(GroupTreeNode.thread_id == thread_id))
        return {n.node_index: n for n in rows}

    async def set_tree_node(self, thread_id: str, index: int, public_key: str | None,
                            epoch: int, owner: str | None = None) -> None:
        node = await self.s.get(GroupTreeNode, (thread_id, index))
        if node is None:
            self.s.add(GroupTreeNode(thread_id=thread_id, node_index=index, public_key=public_key,
                                     owner_user_id=owner, epoch=epoch))
        else:
            node.public_key = public_key
            node.epoch = epoch
            if index % 2 == 0:
                node.owner_user_id = owner

    async def commits_since(self, thread_id: str, since_epoch: int, limit: int = 100) -> list[GroupKeyCommit]:
        rows = await self.s.scalars(
            select(GroupKeyCommit).where(GroupKeyCommit.thread_id == thread_id, GroupKeyCommit.epoch > since_epoch)
            .order_by(GroupKeyCommit.epoch).limit(limit)
        )
        return list(rows)

    async def membership_events(self, thread_id: str, *, rotation_status: str | None = None) -> list[GroupMembershipEvent]:
        stmt = select(GroupMembershipEvent).where(GroupMembershipEvent.thread_id == thread_id)
        if rotation_status:
            stmt = stmt.where(GroupMembershipEvent.rotation_status == rotation_status)
        rows = await self.s.scalars(stmt.order_by(GroupMembershipEvent.created_at))
        return list(rows)
