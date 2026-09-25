"""Настройки аккаунта, «Invite link only», чёрный список TON-адресов, жалобы."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Report, User
from app.repositories import MessageRepository, SettingsRepository, UserRepository
from app.schemas.settings import SettingsOut, SettingsPatchIn
from app.schemas.users import ReportIn, UserPublicOut
from app.services.errors import Invalid, NotFound
from app.services.infra import Infra
from app.services.presenters import user_public

DELETE_MEDIA_TASK = "media.delete_all_for_user"


class SettingsService:
    def __init__(self, session: AsyncSession, infra: Infra) -> None:
        self.s = session
        self.infra = infra
        self.repo = SettingsRepository(session)
        self.users = UserRepository(session)

    async def get(self, user: User) -> SettingsOut:
        st = await self.repo.get(user.id)
        cached = await MessageRepository(self.s).cached_media_bytes(user.id)
        return SettingsOut(
            autolock=st.autolock, group_invite_policy=st.group_invite_policy,
            markdown_preview=st.markdown_preview, media_retention=st.media_retention,
            show_online=st.show_online, send_read_receipts=st.send_read_receipts,
            cached_media_bytes=cached,
        )

    async def patch(self, user: User, body: SettingsPatchIn) -> SettingsOut:
        st = await self.repo.get(user.id)
        for field, value in body.model_dump(exclude_none=True).items():
            setattr(st, field, value)
        await self.s.commit()
        return await self.get(user)

    async def delete_media(self, user: User) -> None:
        self.infra.tasks.enqueue(DELETE_MEDIA_TASK, user.id)

    async def _users_public(self, ids: list[str]) -> list[UserPublicOut]:
        users = await self.users.get_many(ids)
        wallets = await self.users.primary_wallets(ids)
        return [user_public(users[i], wallets.get(i)) for i in ids if i in users]

    async def _require_user(self, user: User, other_id: str) -> None:
        if other_id == user.id:
            raise Invalid("can't apply this to yourself")
        if await self.users.get(other_id) is None:
            raise NotFound("user not found")

    # --- «Invite link only» ---
    async def allowlist(self, user: User) -> list[UserPublicOut]:
        return await self._users_public(await self.repo.allowlist(user.id))

    async def allow(self, user: User, other_id: str) -> None:
        await self._require_user(user, other_id)
        await self.repo.allow(user.id, other_id)
        await self.s.commit()

    async def disallow(self, user: User, other_id: str) -> None:
        await self.repo.disallow(user.id, other_id)
        await self.s.commit()

    # --- чёрный список ---
    async def blocklist(self, user: User) -> list[UserPublicOut]:
        return await self._users_public(await self.repo.blocked_ids(user.id))

    async def block(self, user: User, other_id: str) -> None:
        await self._require_user(user, other_id)
        await self.repo.block(user.id, other_id)
        await self.s.commit()

    async def unblock(self, user: User, other_id: str) -> None:
        await self.repo.unblock(user.id, other_id)
        await self.s.commit()

    # --- жалобы (для модерации в Flask-админке) ---
    async def report(self, user: User, body: ReportIn) -> str:
        if not (body.target_user_id or body.thread_id or body.message_id):
            raise Invalid("report must reference a user, a thread or a message")
        report = Report(reporter_id=user.id, reason=body.reason, details=body.details,
                        target_user_id=body.target_user_id, thread_id=body.thread_id, message_id=body.message_id)
        self.s.add(report)
        await self.s.commit()
        return report.id
