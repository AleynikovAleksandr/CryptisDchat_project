"""Pairing authorization, key pinning, expiry, and durable completion behavior."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from pydantic import ValidationError
from sqlalchemy import select

from app.models import Device, RefreshToken
from app.repositories.database import get_sessionmaker
from app.schemas.device_pairing import PairingEnvelope
from realm import ecies

PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
PUBLIC_KEY = ecies.spki_b64(PRIVATE_KEY.public_key())
OTHER_KEY = ecies.spki_b64(ec.generate_private_key(ec.SECP256R1()).public_key())


async def create(client, **overrides):
    response = await client.post('/api/auth/pairings', json={
        'device_name': 'Firefox on Linux', 'ephemeral_public_key': PUBLIC_KEY, **overrides,
    })
    assert response.status_code == 201, response.text
    return response.json()


def path(pair):
    return f"/api/auth/pairings/{pair['request_id']}"


def secret(pair):
    return {'X-Pairing-Secret': pair['poll_secret']}


def scanned(pair):
    return {'request_id': pair['request_id'], 'ephemeral_public_key': PUBLIC_KEY}


def envelope(pair, user, **overrides):
    bindings = {
        'request_id': pair['request_id'], 'ephemeral_public_key': PUBLIC_KEY,
        'account_id': user.id, 'operation_id': str(uuid.uuid4()),
    }
    return {
        **bindings,
        'ciphertext': ecies.seal(PUBLIC_KEY, json.dumps(bindings).encode()),
        **overrides,
    }


async def approve(client, user, pair):
    body = {**scanned(pair), 'envelope': envelope(pair, user)}
    response = await user.post(path(pair) + '/approve', body)
    assert response.status_code == 200, response.text
    assert 'set-cookie' not in response.headers
    return body['envelope']


async def complete(client, pair, env):
    return await client.post(path(pair) + '/complete', headers=secret(pair),
                             json={'operation_id': env['operation_id']})


async def test_create_pending_hashes_secret_and_pins_client_public_key(client, infra):
    pair = await create(client)
    assert pair['qr_payload'] == {'version': 1, 'request_id': pair['request_id'], 'ephemeral_public_key': PUBLIC_KEY}
    assert len(pair['poll_secret']) >= 32
    state = await infra.redis.get('pairing:' + pair['request_id'])
    assert pair['poll_secret'] not in state
    assert hashlib.sha256(pair['poll_secret'].encode()).hexdigest() in state
    assert 0 < await infra.redis.ttl('pairing:' + pair['request_id']) <= 120
    response = await client.get(path(pair), headers=secret(pair))
    assert response.json() == {'status': 'pending', 'expires_at': pair['expires_at']}
    async with get_sessionmaker()() as session:
        assert not (await session.scalars(select(Device))).all()


@pytest.mark.parametrize('body', [
    {'device_name': '', 'ephemeral_public_key': PUBLIC_KEY},
    {'device_name': 'x' * 101, 'ephemeral_public_key': PUBLIC_KEY},
    {'device_name': 'Browser', 'ephemeral_public_key': 'invalid'},
    {'device_name': 'Browser'},
])
async def test_create_rejects_invalid_name_or_public_key(client, body):
    assert (await client.post('/api/auth/pairings', json=body)).status_code == 422


async def test_poll_secret_required_for_poll_cancel_and_complete(client):
    pair = await create(client)
    for method, suffix, body in [('GET', '', None), ('DELETE', '', None), ('POST', '/complete', {'operation_id': str(uuid.uuid4())})]:
        response = await client.request(method, path(pair) + suffix, headers={'X-Pairing-Secret': 'wrong'}, json=body)
        assert response.status_code == 401
    assert (await client.get(path(pair))).status_code == 401


async def test_cancel_pending_pairing_refunds_its_generation_slot(client):
    cancelled = await create(client)
    assert cancelled['generation_remaining'] == 2
    response = await client.delete(path(cancelled), headers=secret(cancelled))
    assert response.status_code == 204

    remaining = []
    for expected in (2, 1, 0):
        pair = await create(client)
        remaining.append(pair['generation_remaining'])
        assert pair['generation_remaining'] == expected
    assert remaining == [2, 1, 0]
    blocked = await client.post('/api/auth/pairings', json={
        'device_name': 'Firefox on Linux', 'ephemeral_public_key': PUBLIC_KEY,
    })
    assert blocked.status_code == 429


async def test_approval_and_inspection_require_auth_and_pin_scanned_key(client, make_user):
    user = await make_user()
    pair = await create(client)
    for action in ['inspect', 'approve', 'reject']:
        body = {**scanned(pair), 'envelope': envelope(pair, user)} if action == 'approve' else scanned(pair)
        assert (await client.post(path(pair) + '/' + action, json=body)).status_code == 401
        body['ephemeral_public_key'] = OTHER_KEY
        assert (await user.post(path(pair) + '/' + action, body)).status_code == 409
    result = await user.post(path(pair) + '/inspect', scanned(pair))
    assert result.json()['device_name'] == 'Firefox on Linux'
    assert set(result.json()) == {'device_name', 'expires_at'}


async def test_inspection_binds_account_and_other_account_cannot_approve_or_reject(client, make_user):
    alice, bob = await make_user(), await make_user()
    pair = await create(client)
    assert (await alice.post(path(pair) + '/inspect', scanned(pair))).status_code == 200
    for action in ['inspect', 'approve', 'reject']:
        body = {**scanned(pair), 'envelope': envelope(pair, bob)} if action == 'approve' else scanned(pair)
        assert (await bob.post(path(pair) + '/' + action, body)).status_code == 403
    assert (await client.get(path(pair), headers=secret(pair))).json()['status'] == 'pending'


@pytest.mark.parametrize('field,value', [('request_id', str(uuid.uuid4())), ('account_id', str(uuid.uuid4())), ('ephemeral_public_key', OTHER_KEY)])
async def test_ciphertext_envelope_must_match_request_account_and_scanned_key(client, make_user, field, value):
    user = await make_user()
    pair = await create(client)
    response = await user.post(path(pair) + '/approve', {
        **scanned(pair), 'envelope': envelope(pair, user, **{field: value}),
    })
    assert response.status_code in (403, 409)
    assert (await client.get(path(pair), headers=secret(pair))).json()['status'] == 'pending'


async def test_approve_returns_only_ciphertext_to_holder_and_does_not_issue_session(client, make_user, infra):
    user = await make_user()
    pair = await create(client)
    env = await approve(client, user, pair)
    response = await client.get(path(pair), headers=secret(pair))
    assert response.json()['envelope'] == env
    assert response.json()['status'] == 'approved'
    assert len((await user.get('/api/auth/sessions')).json()) == 1
    assert (await user.post(path(pair) + '/approve', {**scanned(pair), 'envelope': env})).status_code == 409
    state = await infra.redis.get('pairing:' + pair['request_id'])
    assert 'refresh_token' not in state and user.access not in state and user.refresh not in state


@pytest.mark.parametrize('action', ['reject', 'cancel', 'expire'])
async def test_terminal_requests_cannot_complete_or_deliver_ciphertext(client, make_user, infra, action):
    user = await make_user()
    pair = await create(client)
    env = envelope(pair, user)
    if action == 'reject':
        assert (await user.post(path(pair) + '/reject', scanned(pair))).status_code == 200
    elif action == 'cancel':
        await approve(client, user, pair)
        assert (await client.delete(path(pair), headers=secret(pair))).status_code == 204
    else:
        await infra.redis.delete('pairing:' + pair['request_id'])
    response = await client.get(path(pair), headers=secret(pair))
    assert response.json()['status'] == ('expired' if action == 'expire' else 'rejected')
    assert 'envelope' not in response.json()
    assert (await complete(client, pair, env)).status_code == 409
    assert len((await user.get('/api/auth/sessions')).json()) == 1


async def test_completion_binds_operation_installs_cookie_and_retries_same_device(client, make_user, infra):
    user = await make_user()
    pair = await create(client)
    env = await approve(client, user, pair)
    wrong = await complete(client, pair, {**env, 'operation_id': str(uuid.uuid4())})
    assert wrong.status_code == 409
    response = await complete(client, pair, env)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['user']['id'] == user.id and body['is_new_user'] is False
    cookie = response.cookies['cx_device']
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    assert 'Path=/api/auth' in response.headers['set-cookie']
    retry = await complete(client, pair, env)
    assert retry.status_code == 200, retry.text
    assert retry.json()['refresh_token'] == body['refresh_token']
    assert retry.cookies['cx_device'] == cookie
    sessions = (await user.get('/api/auth/sessions')).json()
    assert len(sessions) == 2
    assert [s['name'] for s in sessions if not s['current']] == ['Firefox on Linux']
    assert (await client.get('/api/me', headers={'Authorization': 'Bearer ' + body['access_token']})).status_code == 200
    assert (await client.get(path(pair), headers=secret(pair))).status_code == 409
    state = await infra.redis.get('pairing:' + pair['request_id'])
    for value in [body['access_token'], body['refresh_token'], cookie, pair['poll_secret'], env['ciphertext']]:
        assert value not in state


async def test_completion_replay_after_refresh_rotation_does_not_resurrect_credentials(client, make_user):
    user = await make_user()
    pair = await create(client)
    env = await approve(client, user, pair)
    first = await complete(client, pair, env)
    assert first.status_code == 200
    assert (await client.post('/api/auth/refresh', json={'refresh_token': first.json()['refresh_token']})).status_code == 200
    assert (await complete(client, pair, env)).status_code == 409
    assert len((await user.get('/api/auth/sessions')).json()) == 2


async def test_failed_redis_finalization_rolls_back_new_device(client, make_user, infra, monkeypatch):
    user = await make_user()
    pair = await create(client)
    env = await approve(client, user, pair)
    # Force the actual Redis transaction to fail only when publishing completion.
    from redis.asyncio.client import Pipeline
    original = Pipeline.execute

    async def fail_completion(self, *args, **kwargs):
        if any('"completed"' in str(command) for command, _ in self.command_stack):
            raise ConnectionError('Redis unavailable during finalization')
        return await original(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Pipeline, 'execute', fail_completion)
        with pytest.raises(ConnectionError):
            await complete(client, pair, env)
    assert len((await user.get('/api/auth/sessions')).json()) == 1
    response = await complete(client, pair, env)
    assert response.status_code == 200, response.text
    assert len((await user.get('/api/auth/sessions')).json()) == 2


async def test_concurrent_approval_and_rejection_have_one_winner(client, make_user):
    user = await make_user()
    pair = await create(client)
    responses = await asyncio.gather(
        user.post(path(pair) + '/approve', {**scanned(pair), 'envelope': envelope(pair, user)}),
        user.post(path(pair) + '/reject', scanned(pair)),
    )
    assert sorted(r.status_code for r in responses) == [200, 409]


async def test_pairing_creation_has_bounded_rate_limit(client):
    for _ in range(5):
        await create(client)
    response = await client.post('/api/auth/pairings', json={'device_name': 'Linux', 'ephemeral_public_key': PUBLIC_KEY})
    assert response.status_code == 429


async def test_concurrent_completion_never_adds_two_devices(client, make_user):
    user = await make_user()
    pair = await create(client)
    env = await approve(client, user, pair)
    results = await asyncio.gather(complete(client, pair, env), complete(client, pair, env))
    assert all(response.status_code in (200, 409) for response in results)
    assert any(response.status_code == 200 for response in results)
    retry = await complete(client, pair, env)
    assert retry.status_code == 200
    first = next(response for response in results if response.status_code == 200)
    assert retry.json() == first.json()
    assert len((await user.get('/api/auth/sessions')).json()) == 2
    async with get_sessionmaker()() as session:
        tokens = (await session.scalars(select(RefreshToken).where(RefreshToken.device_id == pair['request_id']))).all()
        assert len(tokens) == 1


async def test_sql_commit_failure_after_redis_receipt_is_retryable(client, make_user, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    user = await make_user()
    pair = await create(client)
    env = await approve(client, user, pair)

    async def unavailable(self):
        raise ConnectionError('database commit unavailable')

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, 'commit', unavailable)
        with pytest.raises(ConnectionError):
            await complete(client, pair, env)
    assert len((await user.get('/api/auth/sessions')).json()) == 1
    assert (await complete(client, pair, env)).status_code == 200
    assert len((await user.get('/api/auth/sessions')).json()) == 2


async def test_expiry_during_completion_cannot_commit_provisional_session(client, make_user, infra, monkeypatch):
    from app.services.auth_service import AuthService

    user = await make_user()
    pair = await create(client)
    env = await approve(client, user, pair)
    original = AuthService.prepare_pairing_session

    async def expire_after_prepare(self, *args, **kwargs):
        issued = await original(self, *args, **kwargs)
        await infra.redis.delete('pairing:' + pair['request_id'])
        return issued

    monkeypatch.setattr(AuthService, 'prepare_pairing_session', expire_after_prepare)
    assert (await complete(client, pair, env)).status_code == 409
    assert len((await user.get('/api/auth/sessions')).json()) == 1


async def test_revoked_paired_device_cannot_be_resurrected_by_completion_retry(client, make_user):
    user = await make_user()
    pair = await create(client)
    env = await approve(client, user, pair)
    result = await complete(client, pair, env)
    assert result.status_code == 200
    assert (await user.delete('/api/auth/sessions/' + pair['request_id'])).status_code == 204
    assert (await complete(client, pair, env)).status_code == 409
    assert len((await user.get('/api/auth/sessions')).json()) == 1


@pytest.mark.parametrize('key', [
    base64.b64encode(bytes(range(32))).decode(),
    ecies.spki_b64(ec.generate_private_key(ec.SECP384R1()).public_key()),
    ecies.spki_b64(ec.generate_private_key(ec.SECP256K1()).public_key()),
    base64.b64encode(ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
    )).decode(),
    base64.b64encode(base64.b64decode(PUBLIC_KEY)[:26] + b'\x04' + bytes(64)).decode(),
    base64.b64encode(base64.b64decode(PUBLIC_KEY) + b'\x00').decode(),
    PUBLIC_KEY[:-3] + 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'[
        'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'.index(PUBLIC_KEY[-3]) + 1
    ] + '==',
], ids=['raw-x25519', 'p384', 'secp256k1', 'ed25519', 'invalid-point', 'trailing-der', 'noncanonical-base64'])
async def test_create_rejects_keys_incompatible_with_browser_ecies(client, key):
    response = await client.post('/api/auth/pairings', json={
        'device_name': 'Browser', 'ephemeral_public_key': key,
    })
    assert response.status_code == 422


@pytest.mark.parametrize('size,version,valid_point,expected_valid', [
    (93, 1, True, False),
    (94, 1, True, True),
    (95, 1, True, True),
    (94, 0, True, False),
    (94, 2, True, False),
    (94, 1, False, False),
])
def test_envelope_requires_browser_ecies_framing(size, version, valid_point, expected_valid):
    # Real ECIES bytes at the exact empty/one-byte plaintext boundary.
    blob = bytearray(base64.b64decode(ecies.seal(PUBLIC_KEY, b'x' * max(0, size - 94))))
    blob = blob[:size]
    blob[0] = version
    if not valid_point:
        blob[1:66] = b'\x04' + bytes(64)
    ciphertext = base64.b64encode(blob).decode()
    fields = {
        'request_id': str(uuid.uuid4()), 'ephemeral_public_key': PUBLIC_KEY,
        'account_id': str(uuid.uuid4()), 'operation_id': str(uuid.uuid4()), 'ciphertext': ciphertext,
    }
    if expected_valid:
        assert PairingEnvelope(**fields).ciphertext == ciphertext
        assert ecies.open_sealed(PRIVATE_KEY, ciphertext) == b'x' * (size - 94)
    else:
        with pytest.raises(ValidationError):
            PairingEnvelope(**fields)


async def test_approval_relays_decryptable_existing_ecies_format(client, make_user):
    user = await make_user()
    pair = await create(client)
    env = await approve(client, user, pair)
    response = await client.get(path(pair), headers=secret(pair))
    plain = json.loads(ecies.open_sealed(PRIVATE_KEY, response.json()['envelope']['ciphertext']))
    assert plain == {field: env[field] for field in (
        'request_id', 'ephemeral_public_key', 'account_id', 'operation_id',
    )}
