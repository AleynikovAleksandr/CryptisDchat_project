"""Обмен криптографическим материалом (ТЗ 3, 6): публичные ключи, обёрнутые Conversation key,
дерево ключей групп, резервное копирование и восстановление ключей."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, current_user, get_infra, get_session
from app.schemas.keys import (
    BackupIn,
    BackupMetaOut,
    ConversationKeyOut,
    ConversationKeysIn,
    KeySetIn,
    KeySetOut,
    RealmOut,
    RecoveryIn,
    RecoveryOut,
    TreeCommitIn,
    TreeCommitOut,
    TreePlanOut,
)
from app.services.infra import Infra
from app.services.key_service import KeyService

router = APIRouter(prefix="/api", tags=["keys"])


def svc(session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)) -> KeyService:
    return KeyService(session, infra)


# --- публичные ключи ---
@router.post("/keys", status_code=201)
async def register_keys(body: KeySetIn, cu: CurrentUser = Depends(current_user), s: KeyService = Depends(svc)):
    keyset, refresh_groups = await s.register(cu.user, body)
    return {"keys": keyset, "refresh_groups": refresh_groups}


@router.get("/keys/realms", response_model=list[RealmOut])
async def realms(cu: CurrentUser = Depends(current_user), s: KeyService = Depends(svc)):
    return await s.realms()


@router.get("/keys/backup", response_model=BackupMetaOut)
async def backup_meta(cu: CurrentUser = Depends(current_user), s: KeyService = Depends(svc)):
    return await s.backup_meta(cu.user)


@router.post("/keys/backup", response_model=BackupMetaOut, status_code=201)
async def create_backup(body: BackupIn, cu: CurrentUser = Depends(current_user), s: KeyService = Depends(svc)):
    return await s.create_backup(cu.user, body)


@router.post("/keys/recovery", response_model=RecoveryOut, status_code=202)
async def start_recovery(body: RecoveryIn, cu: CurrentUser = Depends(current_user), s: KeyService = Depends(svc)):
    return await s.start_recovery(cu.user, cu.device_id, body)


@router.get("/keys/recovery/{request_id}", response_model=RecoveryOut)
async def recovery_status(request_id: str, cu: CurrentUser = Depends(current_user), s: KeyService = Depends(svc)):
    return await s.recovery_status(cu.user, request_id)


@router.delete("/keys/recovery/{request_id}", status_code=204)
async def recovery_done(request_id: str, cu: CurrentUser = Depends(current_user), s: KeyService = Depends(svc)):
    await s.mark_backup_restored(cu.user, request_id)


@router.get("/keys/{user_id}", response_model=KeySetOut)
async def user_keys(user_id: str, version: int | None = None, cu: CurrentUser = Depends(current_user),
                    s: KeyService = Depends(svc)):
    return await s.get_keys(user_id, version)


# --- Conversation key личных чатов ---
@router.get("/threads/{thread_id}/keys", response_model=list[ConversationKeyOut])
async def conversation_keys(thread_id: str, cu: CurrentUser = Depends(current_user), s: KeyService = Depends(svc)):
    return await s.conversation_keys(cu.user, thread_id)


@router.post("/threads/{thread_id}/keys", status_code=201)
async def upload_conversation_keys(thread_id: str, body: ConversationKeysIn, cu: CurrentUser = Depends(current_user),
                                   s: KeyService = Depends(svc)):
    return {"epoch": await s.upload_conversation_keys(cu.user, thread_id, body)}


# --- дерево ключей группы ---
@router.get("/groups/{group_id}/keys/plan", response_model=TreePlanOut)
async def tree_plan(group_id: str, periodic: bool = False, cu: CurrentUser = Depends(current_user),
                    s: KeyService = Depends(svc)):
    return await s.tree_plan(cu.user, group_id, periodic)


@router.post("/groups/{group_id}/keys/commit", status_code=201)
async def tree_commit(group_id: str, body: TreeCommitIn, periodic: bool = False,
                      cu: CurrentUser = Depends(current_user), s: KeyService = Depends(svc)):
    return {"epoch": await s.tree_commit(cu.user, group_id, body, periodic)}


@router.get("/groups/{group_id}/keys/commits", response_model=list[TreeCommitOut])
async def tree_commits(group_id: str, since: int = 0, cu: CurrentUser = Depends(current_user),
                       s: KeyService = Depends(svc)):
    return await s.tree_commits(cu.user, group_id, since)
