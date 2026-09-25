from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BlockedUser, GroupInviteAllow, UserSettings


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, user_id: str) -> UserSettings:
        settings = await self.s.get(UserSettings, user_id)
        if settings is None:
            settings = UserSettings(user_id=user_id)
            self.s.add(settings)
            await self.s.flush()
        return settings

    async def get_many(self, user_ids: list[str]) -> dict[str, UserSettings]:
        if not user_ids:
            return {}
        rows = await self.s.scalars(select(UserSettings).where(UserSettings.user_id.in_(set(user_ids))))
        found = {r.user_id: r for r in rows}
        for uid in user_ids:
            found.setdefault(uid, UserSettings(user_id=uid, group_invite_policy="everyone"))
        return found

    # --- «Invite link only» ---
    async def allowlist(self, user_id: str) -> list[str]:
        rows = await self.s.scalars(select(GroupInviteAllow.allowed_user_id).where(GroupInviteAllow.user_id == user_id))
        return list(rows)

    async def is_allowed_inviter(self, user_id: str, inviter_id: str) -> bool:
        return await self.s.get(GroupInviteAllow, (user_id, inviter_id)) is not None

    async def allow(self, user_id: str, allowed_id: str) -> None:
        if await self.s.get(GroupInviteAllow, (user_id, allowed_id)) is None:
            self.s.add(GroupInviteAllow(user_id=user_id, allowed_user_id=allowed_id))

    async def disallow(self, user_id: str, allowed_id: str) -> None:
        await self.s.execute(
            delete(GroupInviteAllow).where(GroupInviteAllow.user_id == user_id, GroupInviteAllow.allowed_user_id == allowed_id)
        )

    # --- чёрный список ---
    async def blocked_ids(self, user_id: str) -> list[str]:
        rows = await self.s.scalars(
            select(BlockedUser.blocked_user_id).where(BlockedUser.user_id == user_id).order_by(BlockedUser.created_at)
        )
        return list(rows)

    async def block(self, user_id: str, blocked_id: str) -> None:
        if await self.s.get(BlockedUser, (user_id, blocked_id)) is None:
            self.s.add(BlockedUser(user_id=user_id, blocked_user_id=blocked_id))

    async def unblock(self, user_id: str, blocked_id: str) -> None:
        await self.s.execute(
            delete(BlockedUser).where(BlockedUser.user_id == user_id, BlockedUser.blocked_user_id == blocked_id)
        )

    async def is_blocked_between(self, a: str, b: str) -> bool:
        """True, если один из двух пользователей заблокировал другого."""
        found = await self.s.scalar(
            select(BlockedUser.user_id).where(
                or_(
                    (BlockedUser.user_id == a) & (BlockedUser.blocked_user_id == b),
                    (BlockedUser.user_id == b) & (BlockedUser.blocked_user_id == a),
                )
            ).limit(1)
        )
        return found is not None
