from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, PushToken, RefreshToken, User, UserSettings, UserWallet, utcnow


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    # --- пользователи ---
    async def get(self, user_id: str) -> User | None:
        return await self.s.get(User, user_id)

    async def get_many(self, user_ids: list[str]) -> dict[str, User]:
        if not user_ids:
            return {}
        rows = await self.s.scalars(select(User).where(User.id.in_(set(user_ids))))
        return {u.id: u for u in rows}

    async def by_username(self, username: str) -> User | None:
        return await self.s.scalar(select(User).where(User.username == username))

    async def search(self, query: str, exclude_id: str, limit: int = 30) -> list[User]:
        q = query.strip().lstrip("@").lower()
        stmt = select(User).where(User.id != exclude_id, User.is_suspended.is_(False))
        if q:
            like = f"%{q}%"
            stmt = stmt.outerjoin(UserWallet, UserWallet.user_id == User.id).where(
                or_(
                    User.display_name.ilike(like),
                    User.username.ilike(like),
                    UserWallet.address.ilike(like),
                    UserWallet.friendly_address.ilike(like),
                )
            ).distinct()
        stmt = stmt.order_by(User.display_name).limit(limit)
        return list(await self.s.scalars(stmt))

    async def create(self, display_name: str, tint: str) -> User:
        user = User(display_name=display_name, avatar_tint=tint)
        self.s.add(user)
        await self.s.flush()
        self.s.add(UserSettings(user_id=user.id))
        return user

    # --- кошельки ---
    async def wallet_by_address(self, address: str) -> UserWallet | None:
        return await self.s.scalar(select(UserWallet).where(UserWallet.address == address))

    async def wallets(self, user_id: str) -> list[UserWallet]:
        rows = await self.s.scalars(
            select(UserWallet).where(UserWallet.user_id == user_id).order_by(UserWallet.linked_at)
        )
        return list(rows)

    async def primary_wallets(self, user_ids: list[str]) -> dict[str, UserWallet]:
        if not user_ids:
            return {}
        rows = await self.s.scalars(
            select(UserWallet).where(UserWallet.user_id.in_(set(user_ids)), UserWallet.is_primary.is_(True))
        )
        return {w.user_id: w for w in rows}

    async def add_wallet(self, user_id: str, address: str, friendly: str, public_key: str, network: str) -> UserWallet:
        await self.s.execute(update(UserWallet).where(UserWallet.user_id == user_id).values(is_primary=False))
        wallet = UserWallet(
            user_id=user_id, address=address, friendly_address=friendly,
            public_key=public_key, network=network, is_primary=True,
        )
        self.s.add(wallet)
        await self.s.flush()
        return wallet

    # --- устройства и refresh-токены ---
    async def create_device(self, user_id: str, name: str, user_agent: str, ip: str) -> Device:
        device = Device(user_id=user_id, name=name[:100], user_agent=user_agent[:255], ip_address=ip[:45])
        self.s.add(device)
        await self.s.flush()
        return device

    async def get_device(self, device_id: str) -> Device | None:
        return await self.s.get(Device, device_id)

    async def active_devices(self, user_id: str) -> list[Device]:
        rows = await self.s.scalars(
            select(Device).where(Device.user_id == user_id, Device.revoked_at.is_(None)).order_by(Device.last_seen_at.desc())
        )
        return list(rows)

    async def add_refresh_token(self, user_id: str, device_id: str, token_hash: str, expires_at: datetime) -> RefreshToken:
        token = RefreshToken(user_id=user_id, device_id=device_id, token_hash=token_hash, expires_at=expires_at)
        self.s.add(token)
        await self.s.flush()
        return token

    async def refresh_by_hash(self, token_hash: str) -> RefreshToken | None:
        return await self.s.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    async def revoke_device(self, device_id: str) -> None:
        now = utcnow()
        await self.s.execute(update(Device).where(Device.id == device_id).values(revoked_at=now))
        await self.s.execute(
            update(RefreshToken).where(RefreshToken.device_id == device_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def revoke_all(self, user_id: str) -> list[str]:
        now = utcnow()
        device_ids = [d.id for d in await self.active_devices(user_id)]
        await self.s.execute(update(User).where(User.id == user_id).values(sessions_revoked_at=now))
        await self.s.execute(
            update(Device).where(Device.user_id == user_id, Device.revoked_at.is_(None)).values(revoked_at=now)
        )
        await self.s.execute(
            update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return device_ids

    async def upsert_push_token(self, user_id: str, device_id: str, platform: str, token: str) -> None:
        existing = await self.s.scalar(
            select(PushToken).where(PushToken.device_id == device_id, PushToken.platform == platform)
        )
        if existing:
            existing.token = token
        else:
            self.s.add(PushToken(user_id=user_id, device_id=device_id, platform=platform, token=token))
