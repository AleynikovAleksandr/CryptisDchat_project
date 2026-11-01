"""Twenty-minute encrypted relay; Redis WATCH/MULTI serializes every transition.

Private ephemeral/chat keys never enter this service. A completed receipt retains
only public metadata and a secret hash for exact completion retries until expiry.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from redis.exceptions import WatchError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, DevicePairingQuota, User
from app.schemas.device_pairing import PairingApproveIn, PairingCreateIn, PairingScanIn
from app.services.auth_service import AuthService, IssuedSession
from app.services.errors import Conflict, Forbidden, TooManyRequests, Unauthorized
from app.services.infra import Infra

TTL = 20 * 60
GENERATION_LIMIT = 3


class DevicePairingService:
    def __init__(self, session: AsyncSession, infra: Infra) -> None:
        self.s = session
        self.infra = infra

    async def limit(self, identity: str, action: str, count: int, window_seconds: int = 60) -> int:
        result = await self.infra.limiter.hit(f'pairing:{identity}:{action}', count, window_seconds)
        if not result.allowed:
            extra = {'retry_after': result.retry_after}
            if action == 'create':
                extra.update({
                    'generation_limit': count,
                    'generation_remaining': 0,
                    'generation_window_seconds': window_seconds,
                })
            raise TooManyRequests('too many pairing requests', extra=extra)
        return result.remaining

    def _generation_identity_hash(self, identity: str) -> str:
        return hmac.new(
            self.infra.settings.jwt_secret.encode(),
            ('device-pairing-generation:' + identity).encode(),
            hashlib.sha256,
        ).hexdigest()

    async def reserve_generation(self, identity: str, request_id: str) -> int:
        """Persist QR generation slots by pairing ID so canceled requests can be refunded."""
        now = time.time()
        cutoff = now - TTL
        identity_hash = self._generation_identity_hash(identity)
        cutoff_datetime = datetime.fromtimestamp(cutoff, timezone.utc).replace(tzinfo=None)
        now_datetime = datetime.fromtimestamp(now, timezone.utc).replace(tzinfo=None)

        for attempt in range(2):
            try:
                async with self.s.begin():
                    await self.s.execute(delete(DevicePairingQuota).where(
                        DevicePairingQuota.updated_at < cutoff_datetime,
                    ))
                    quota = await self.s.scalar(
                        select(DevicePairingQuota)
                        .where(DevicePairingQuota.identity_hash == identity_hash)
                        .with_for_update()
                    )
                    if quota is None:
                        quota = DevicePairingQuota(
                            identity_hash=identity_hash, attempts=[], updated_at=now_datetime,
                        )
                        self.s.add(quota)
                        await self.s.flush()

                    active = []
                    for attempt in quota.attempts or []:
                        timestamp = attempt.get('timestamp') if isinstance(attempt, dict) else attempt
                        if isinstance(timestamp, (int, float)) and float(timestamp) > cutoff:
                            active.append((float(timestamp), attempt))
                    active.sort(key=lambda item: item[0])
                    if len(active) >= GENERATION_LIMIT:
                        retry_after = max(1, math.ceil(TTL - (now - active[0][0])))
                        raise TooManyRequests('too many pairing requests', extra={
                            'retry_after': retry_after,
                            'generation_limit': GENERATION_LIMIT,
                            'generation_remaining': 0,
                            'generation_window_seconds': TTL,
                        })

                    active.append((now, {'request_id': request_id, 'timestamp': now}))
                    quota.attempts = [attempt for _, attempt in active]
                    quota.updated_at = now_datetime
                    remaining = GENERATION_LIMIT - len(active)
                return remaining
            except IntegrityError:
                if attempt:
                    raise
                # A concurrent first request may have created this IP's quota row.
                await self.s.rollback()
        raise RuntimeError('could not reserve a pairing generation slot')

    async def release_generation(self, identity_hash: str, request_id: str) -> None:
        """Return a generation slot only for the exact QR request that was canceled."""
        now = time.time()
        cutoff = now - TTL
        cutoff_datetime = datetime.fromtimestamp(cutoff, timezone.utc).replace(tzinfo=None)
        now_datetime = datetime.fromtimestamp(now, timezone.utc).replace(tzinfo=None)
        async with self.s.begin():
            quota = await self.s.scalar(
                select(DevicePairingQuota)
                .where(DevicePairingQuota.identity_hash == identity_hash)
                .with_for_update()
            )
            if quota is None:
                return
            quota.attempts = [
                attempt for attempt in (quota.attempts or [])
                if not (
                    isinstance(attempt, dict)
                    and attempt.get('request_id') == request_id
                    and isinstance(attempt.get('timestamp'), (int, float))
                    and float(attempt['timestamp']) > cutoff
                )
            ]
            quota.updated_at = now_datetime

    async def create(self, body: PairingCreateIn, identity: str) -> dict:
        request_id, poll_secret = str(uuid.uuid4()), secrets.token_urlsafe(32)
        identity_hash = self._generation_identity_hash(identity)
        remaining = await self.reserve_generation(identity, request_id)
        expires_at = int(time.time()) + TTL
        state = {
            'request_id': request_id, 'ephemeral_public_key': body.ephemeral_public_key,
            'device_name': body.device_name, 'expires_at': expires_at, 'status': 'pending',
            'poll_hash': hashlib.sha256(poll_secret.encode()).hexdigest(),
            'generation_identity_hash': identity_hash,
        }
        try:
            await self.infra.redis.set('pairing:' + request_id, json.dumps(state), ex=TTL, nx=True)
        except Exception:
            await self.release_generation(identity_hash, request_id)
            raise
        return {
            'request_id': request_id, 'poll_secret': poll_secret, 'expires_at': expires_at,
            'qr_payload': {'version': 1, 'request_id': request_id,
                           'ephemeral_public_key': body.ephemeral_public_key},
            'generation_limit': GENERATION_LIMIT,
            'generation_remaining': remaining,
            'generation_window_seconds': TTL,
        }

    @staticmethod
    def _secret(state: dict, poll_secret: str | None) -> None:
        if not poll_secret or len(poll_secret) > 128 or not hmac.compare_digest(
            state['poll_hash'], hashlib.sha256(poll_secret.encode()).hexdigest(),
        ):
            raise Unauthorized('invalid pairing secret', code='pairing_secret_invalid')

    @staticmethod
    def _scan(state: dict, body: PairingScanIn, user: User) -> None:
        if body.request_id != state['request_id'] or body.ephemeral_public_key != state['ephemeral_public_key']:
            raise Conflict('scanned QR does not match request', code='pairing_key_mismatch')
        if state.get('account_id') not in (None, user.id):
            raise Forbidden('pairing belongs to another account', code='pairing_account_mismatch')

    @staticmethod
    def _pending(state: dict) -> None:
        if state['status'] != 'pending':
            raise Conflict('pairing is no longer pending', code='pairing_used')

    async def _update(self, request_id: str, change: Callable[[dict], Awaitable[object]]) -> object:
        key = 'pairing:' + request_id
        for _ in range(4):
            async with self.infra.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        raise Conflict('pairing expired', code='pairing_expired')
                    state = json.loads(raw)
                    if time.time() >= state['expires_at']:
                        raise Conflict('pairing expired', code='pairing_expired')
                    result = await change(state)
                    if time.time() >= state['expires_at']:
                        raise Conflict('pairing expired', code='pairing_expired')
                    pipe.multi()
                    pipe.set(key, json.dumps(state), pxat=state['expires_at'] * 1000)
                    await pipe.execute()
                    return result
                except WatchError:
                    # Completion can have uncommitted SQL work; its caller rolls it
                    # back and asks the browser to retry with the same operation ID.
                    if self.s.new or self.s.dirty or self.s.in_transaction():
                        raise Conflict('pairing changed; retry', code='pairing_retry') from None
        raise Conflict('pairing changed; retry', code='pairing_retry')

    async def poll(self, request_id: str, poll_secret: str | None) -> dict:
        if not poll_secret or len(poll_secret) > 128:
            raise Unauthorized('invalid pairing secret', code='pairing_secret_invalid')
        raw = await self.infra.redis.get('pairing:' + request_id)
        if raw is None:
            return {'status': 'expired'}
        state = json.loads(raw)
        self._secret(state, poll_secret)
        if time.time() >= state['expires_at']:
            return {'status': 'expired'}
        if state['status'] == 'completed':
            raise Conflict('pairing already completed', code='pairing_used')
        result = {'status': state['status'], 'expires_at': state['expires_at']}
        if state['status'] == 'approved':
            result['envelope'] = state['envelope']
        return result

    async def inspect(self, request_id: str, body: PairingScanIn, user: User) -> dict:
        async def change(state):
            self._scan(state, body, user)
            self._pending(state)
            state['account_id'] = user.id
            return {'device_name': state['device_name'], 'expires_at': state['expires_at']}
        return await self._update(request_id, change)

    async def approve(self, request_id: str, body: PairingApproveIn, user: User) -> dict:
        async def change(state):
            self._scan(state, body, user)
            self._pending(state)
            self._scan(state, body.envelope, user)
            if body.envelope.account_id != user.id:
                raise Forbidden('envelope account mismatch', code='pairing_account_mismatch')
            state.update(status='approved', account_id=user.id,
                         envelope=body.envelope.model_dump(), operation_id=body.envelope.operation_id)
            return {'status': 'approved'}
        return await self._update(request_id, change)

    async def reject(self, request_id: str, body: PairingScanIn, user: User) -> dict:
        async def change(state):
            self._scan(state, body, user)
            self._pending(state)
            state.update(status='rejected', account_id=user.id)
            state.pop('envelope', None)
            return {'status': 'rejected'}
        return await self._update(request_id, change)

    async def cancel(self, request_id: str, poll_secret: str | None) -> None:
        async def change(state):
            self._secret(state, poll_secret)
            if state['status'] == 'completed':
                raise Conflict('pairing already completed', code='pairing_used')
            if state['status'] == 'pending' and state.get('generation_identity_hash'):
                state['quota_refund_pending'] = True
            state['status'] = 'rejected'
            state.pop('envelope', None)
            if state.get('quota_refund_pending'):
                return state['generation_identity_hash']
            return None

        identity_hash = await self._update(request_id, change)
        if identity_hash:
            await self.release_generation(identity_hash, request_id)

            async def clear_refund(state):
                self._secret(state, poll_secret)
                state['quota_refund_pending'] = False

            await self._update(request_id, clear_refund)

    async def complete(self, request_id: str, poll_secret: str | None, operation_id: str,
                       user_agent: str, ip: str) -> IssuedSession:
        try:
            # Find the account before locking it; revalidate all state under WATCH
            # after acquiring the same user lock used by session revocation.
            raw = await self.infra.redis.get('pairing:' + request_id)
            if raw is None:
                raise Conflict('pairing expired', code='pairing_expired')
            initial = json.loads(raw)
            self._secret(initial, poll_secret)
            if initial['status'] not in ('approved', 'completed'):
                raise Conflict('pairing is not approved', code='pairing_not_approved')
            user = await self.s.scalar(select(User).where(User.id == initial['account_id']).with_for_update())
            if user is None or user.is_suspended:
                raise Forbidden('account unavailable', code='suspended')

            async def change(state):
                self._secret(state, poll_secret)
                if (state['status'] not in ('approved', 'completed')
                        or state.get('operation_id') != operation_id or state.get('account_id') != user.id):
                    raise Conflict('pairing operation mismatch', code='pairing_used')
                state.setdefault('issued_at_ms', int(time.time() * 1000))
                issued = await AuthService(self.s, self.infra).prepare_pairing_session(
                    user, request_id, operation_id, poll_secret, state['issued_at_ms'],
                    state['device_name'], user_agent, ip,
                )
                state['status'] = 'completed'
                state.pop('envelope', None)
                return issued

            issued = await self._update(request_id, change)
            # No active/provisional session exists until Redis has finalized. A
            # failed SQL commit leaves a receipt that can safely retry issuance.
            await self.s.commit()
            return issued
        except IntegrityError:
            await self.s.rollback()
            # The primary key remains a backstop on databases without row locks
            # (SQLite in local development); never mint a second device on retry.
            if await self.s.get(Device, request_id) is not None:
                raise Conflict('completion raced; retry', code='pairing_retry') from None
            raise
        except BaseException:
            await self.s.rollback()
            raise
