"""Вход через TON-кошелёк и сессии (ТЗ 4, 4.2)."""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.interfaces.ton_proof import TonProofRequest, VerifiedWallet
from app.models import User, utcnow
from app.repositories import KeyRepository, UserRepository
from app.schemas.auth import ChallengeOut, SessionOut, TonProofVerifyIn
from app.schemas.users import MeOut
from app.services import security
from app.services.errors import Conflict, Forbidden, Unauthorized
from app.services.infra import Infra
from app.services.presenters import me_out
from app.services.ton_proof import TonProofError, make_payload

TINTS = [
    "hsl(204 60% 45%)", "hsl(265 40% 48%)", "hsl(30 55% 45%)", "hsl(165 45% 38%)",
    "hsl(340 45% 48%)", "hsl(220 30% 40%)", "hsl(12 60% 48%)", "hsl(190 50% 38%)",
]


@dataclass
class IssuedSession:
    user: User
    access_token: str
    access_expires_at: int
    refresh_token: str
    is_new_user: bool


class AuthService:
    def __init__(self, session: AsyncSession, infra: Infra) -> None:
        self.s = session
        self.infra = infra
        self.users = UserRepository(session)

    # ------------------------------------------------------------------ ton_proof
    async def challenge(self) -> ChallengeOut:
        st = self.infra.settings
        payload = make_payload(st.ton_proof_secret, st.ton_proof_ttl_seconds)
        await self.infra.redis.set(f"tonproof:{payload}", "1", ex=st.ton_proof_ttl_seconds)
        return ChallengeOut(
            payload=payload,
            domain=st.ton_proof_domain,
            expires_in=st.ton_proof_ttl_seconds,
            dev_wallet_login=st.dev_wallet_login and st.is_dev,
        )

    async def _verify_proof(self, body: TonProofVerifyIn) -> VerifiedWallet:
        # одноразовость payload: удаляем ключ атомарно; если его не было — повтор или подделка
        if not await self.infra.redis.delete(f"tonproof:{body.proof.payload}"):
            raise Unauthorized("ton_proof payload is unknown or already used", code="proof_invalid")
        req = TonProofRequest(
            address=body.address,
            network=body.network,
            public_key=body.public_key,
            timestamp=body.proof.timestamp,
            domain=body.proof.domain.value,
            domain_length=body.proof.domain.lengthBytes,
            payload=body.proof.payload,
            signature=body.proof.signature,
            state_init=body.proof.state_init,
        )
        try:
            return self.infra.ton_verifier.verify(req)
        except TonProofError as exc:
            raise Unauthorized(str(exc), code="proof_invalid") from exc

    async def login(self, body: TonProofVerifyIn, user_agent: str, ip: str) -> IssuedSession:
        wallet = await self._verify_proof(body)
        existing = await self.users.wallet_by_address(wallet.address)
        is_new = existing is None
        if existing:
            user = await self.users.get(existing.user_id)
            assert user is not None
        else:
            user = await self.users.create(display_name="", tint=random.choice(TINTS))
            short = wallet.friendly_address[:4] + "…" + wallet.friendly_address[-4:]
            user.display_name = short
            await self.users.add_wallet(user.id, wallet.address, wallet.friendly_address, wallet.public_key, wallet.network)
        if user.is_suspended:
            raise Forbidden("account suspended", code="suspended")
        issued = await self._issue(user, body.device_name, user_agent, ip)
        issued.is_new_user = is_new
        await self.s.commit()
        return issued

    async def link_wallet(self, user: User, body: TonProofVerifyIn) -> None:
        """Привязать новый кошелёк (перенос аккаунта, ТЗ 4.2) — после ton_proof нового адреса."""
        wallet = await self._verify_proof(body)
        existing = await self.users.wallet_by_address(wallet.address)
        if existing and existing.user_id != user.id:
            raise Conflict("this wallet is linked to another account", code="wallet_taken")
        if existing is None:
            await self.users.add_wallet(user.id, wallet.address, wallet.friendly_address, wallet.public_key, wallet.network)
        await self.s.commit()

    async def unlink_wallet(self, user: User, address: str) -> None:
        wallets = await self.users.wallets(user.id)
        target = next((w for w in wallets if w.address == address or w.friendly_address == address), None)
        if target is None:
            raise Conflict("wallet is not linked", code="wallet_not_linked")
        if len(wallets) == 1:
            # единственный кошелёк — единственный способ входа, отвязать нельзя
            raise Conflict("cannot unlink the only wallet: it is your login", code="last_wallet")
        await self.s.delete(target)
        await self.s.flush()
        rest = [w for w in wallets if w.id != target.id]
        if not any(w.is_primary for w in rest):
            rest[-1].is_primary = True
        await self.s.commit()

    # ------------------------------------------------------------------ токены
    async def _issue(self, user: User, device_name: str, user_agent: str, ip: str) -> IssuedSession:
        device = await self.users.create_device(user.id, device_name, user_agent, ip)
        refresh = security.new_refresh_token()
        expires = utcnow() + timedelta(days=self.infra.settings.refresh_token_ttl_days)
        await self.users.add_refresh_token(user.id, device.id, security.hash_refresh_token(refresh), expires)
        access, access_exp = security.create_access_token(user.id, device.id)
        return IssuedSession(user=user, access_token=access, access_expires_at=access_exp,
                             refresh_token=refresh, is_new_user=False)

    async def refresh(self, refresh_token: str) -> tuple[str, int, str]:
        """Обмен refresh → новый JWT с ротацией refresh-токена.

        Повторное предъявление уже обменянного токена — признак кражи: сессия устройства отзывается.
        """
        token = await self.users.refresh_by_hash(security.hash_refresh_token(refresh_token))
        if token is None:
            raise Unauthorized("unknown refresh token", code="refresh_invalid")
        now = utcnow()
        if token.revoked_at is not None:
            if token.replaced_by_id is not None:
                await self.users.revoke_device(token.device_id)
                await self.s.commit()
                await self.infra.bus.publish(token.user_id, "session.revoked", {"device_id": token.device_id})
            raise Unauthorized("refresh token revoked", code="refresh_revoked")
        if token.expires_at < now:
            raise Unauthorized("refresh token expired", code="refresh_expired")
        user = await self.users.get(token.user_id)
        device = await self.users.get_device(token.device_id)
        if user is None or user.is_suspended or device is None or device.revoked_at is not None:
            raise Unauthorized("session is no longer valid", code="refresh_revoked")

        new_refresh = security.new_refresh_token()
        new_row = await self.users.add_refresh_token(
            user.id, device.id, security.hash_refresh_token(new_refresh),
            now + timedelta(days=self.infra.settings.refresh_token_ttl_days),
        )
        token.revoked_at = now
        token.last_used_at = now
        token.replaced_by_id = new_row.id
        device.last_seen_at = now
        access, exp = security.create_access_token(user.id, device.id)
        await self.s.commit()
        return access, exp, new_refresh

    async def authenticate(self, access_token: str) -> tuple[User, str]:
        """Проверка JWT. Отзыв проверяется по первичному ключу (ТЗ 4.2)."""
        try:
            claims = security.decode_access_token(access_token)
        except security.TokenError as exc:
            raise Unauthorized("invalid access token", code="token_invalid") from exc
        user = await self.users.get(claims.user_id)
        if user is None or user.is_suspended:
            raise Unauthorized("user is not active", code="token_invalid")
        if security.issued_before(claims, user.sessions_revoked_at):
            raise Unauthorized("session revoked", code="token_revoked")
        device = await self.users.get_device(claims.device_id)
        if device is None or security.issued_before(claims, device.revoked_at):
            raise Unauthorized("session revoked", code="token_revoked")
        return user, claims.device_id

    async def logout(self, user: User, device_id: str) -> None:
        await self.users.revoke_device(device_id)
        await self.s.commit()
        await self.infra.bus.publish(user.id, "session.revoked", {"device_id": device_id})

    async def logout_all(self, user: User) -> None:
        await self.users.revoke_all(user.id)
        await self.s.commit()
        await self.infra.bus.publish(user.id, "session.revoked", {"device_id": "*"})

    async def sessions(self, user: User, current_device: str) -> list[SessionOut]:
        return [
            SessionOut(device_id=d.id, name=d.name, user_agent=d.user_agent, ip_address=d.ip_address,
                       created_at=d.created_at, last_seen_at=d.last_seen_at, current=d.id == current_device)
            for d in await self.users.active_devices(user.id)
        ]

    async def me(self, user: User) -> MeOut:
        keys = KeyRepository(self.s)
        return me_out(user, await self.users.wallets(user.id), await keys.active_set(user.id), await keys.backup(user.id))
