"""Преобразование ORM-объектов в DTO ответа API."""
from __future__ import annotations

import base64

from app.models import KeyBackup, Message, User, UserKeySet, UserWallet
from app.schemas.messages import MessageOut
from app.schemas.users import MeOut, UserPublicOut, WalletOut


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def avatar_url(upload_id: str | None) -> str | None:
    return f"/api/media/avatars/{upload_id}" if upload_id else None


def user_public(user: User, wallet: UserWallet | None = None, online: bool = False) -> UserPublicOut:
    return UserPublicOut(
        id=user.id,
        display_name=user.display_name,
        username=user.username,
        avatar_url=avatar_url(user.avatar_upload_id),
        avatar_tint=user.avatar_tint,
        ton_address_friendly=wallet.friendly_address if wallet else None,
        online=online,
    )


def me_out(user: User, wallets: list[UserWallet], keyset: UserKeySet | None, backup: KeyBackup | None) -> MeOut:
    primary = next((w for w in wallets if w.is_primary), wallets[0] if wallets else None)
    return MeOut(
        id=user.id,
        display_name=user.display_name,
        username=user.username,
        bio=user.bio,
        avatar_url=avatar_url(user.avatar_upload_id),
        avatar_tint=user.avatar_tint,
        ton_address=primary.address if primary else None,
        ton_address_friendly=primary.friendly_address if primary else None,
        wallet_connected=primary is not None,
        wallets=[
            WalletOut(address=w.address, friendly_address=w.friendly_address, network=w.network,
                      is_primary=w.is_primary, linked_at=w.linked_at)
            for w in wallets
        ],
        key_version=keyset.version if keyset else None,
        has_backup=bool(backup and backup.status == "active"),
        backup_status=backup.status if backup else None,
        created_at=user.created_at,
    )


def message_out(m: Message, confirmed: bool = False) -> MessageOut:
    return MessageOut(
        id=m.id,
        thread_id=m.thread_id,
        seq=m.seq,
        sender_id=m.sender_id,
        client_msg_id=m.client_msg_id,
        kind=m.kind,
        ciphertext=b64(m.ciphertext),
        nonce=b64(m.nonce),
        signature=b64(m.signature),
        key_epoch=m.key_epoch,
        sender_key_version=m.sender_key_version,
        reply_to_id=m.reply_to_id,
        forwarded_from_id=m.forwarded_from_id,
        attachment_id=m.attachment_id,
        created_at=m.created_at,
        expires_at=m.expires_at,
        confirmed=confirmed,
    )
