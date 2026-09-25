from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ConversationKey,
    KeyBackup,
    KeyBackupShare,
    KeyRecoveryRequest,
    UserKeySet,
)


class KeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def active_set(self, user_id: str) -> UserKeySet | None:
        return await self.s.scalar(
            select(UserKeySet).where(UserKeySet.user_id == user_id, UserKeySet.is_active.is_(True))
        )

    async def active_sets(self, user_ids: list[str]) -> dict[str, UserKeySet]:
        if not user_ids:
            return {}
        rows = await self.s.scalars(
            select(UserKeySet).where(UserKeySet.user_id.in_(set(user_ids)), UserKeySet.is_active.is_(True))
        )
        return {k.user_id: k for k in rows}

    async def set_version(self, user_id: str, version: int) -> UserKeySet | None:
        return await self.s.scalar(
            select(UserKeySet).where(UserKeySet.user_id == user_id, UserKeySet.version == version)
        )

    async def add_set(self, user_id: str, identity_pub: str, signing_pub: str) -> UserKeySet:
        current = await self.active_set(user_id)
        version = (current.version + 1) if current else 1
        await self.s.execute(update(UserKeySet).where(UserKeySet.user_id == user_id).values(is_active=False))
        ks = UserKeySet(user_id=user_id, version=version, identity_pub=identity_pub, signing_pub=signing_pub)
        self.s.add(ks)
        await self.s.flush()
        return ks

    # --- Conversation keys личных чатов ---
    async def conversation_keys(self, thread_id: str, recipient_id: str) -> list[ConversationKey]:
        rows = await self.s.scalars(
            select(ConversationKey).where(ConversationKey.thread_id == thread_id, ConversationKey.recipient_id == recipient_id)
            .order_by(ConversationKey.epoch)
        )
        return list(rows)

    async def epoch_exists(self, thread_id: str, epoch: int) -> bool:
        found = await self.s.scalar(
            select(ConversationKey.id).where(ConversationKey.thread_id == thread_id, ConversationKey.epoch == epoch).limit(1)
        )
        return found is not None

    # --- резервные копии ---
    async def backup(self, user_id: str) -> KeyBackup | None:
        return await self.s.get(KeyBackup, user_id)

    async def backup_shares(self, user_id: str) -> list[KeyBackupShare]:
        rows = await self.s.scalars(select(KeyBackupShare).where(KeyBackupShare.user_id == user_id))
        return list(rows)

    async def recovery(self, request_id: str) -> KeyRecoveryRequest | None:
        return await self.s.get(KeyRecoveryRequest, request_id)
