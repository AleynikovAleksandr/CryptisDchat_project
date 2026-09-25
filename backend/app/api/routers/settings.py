"""10.7 Настройки аккаунта и 10.9 Чёрный список."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, current_user, get_infra, get_session
from app.schemas.settings import SettingsOut, SettingsPatchIn
from app.schemas.users import UserPublicOut
from app.services.infra import Infra
from app.services.settings_service import SettingsService

router = APIRouter(prefix="/api", tags=["settings"])


def svc(session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)) -> SettingsService:
    return SettingsService(session, infra)


@router.get("/settings", response_model=SettingsOut)
async def get_settings(cu: CurrentUser = Depends(current_user), s: SettingsService = Depends(svc)):
    return await s.get(cu.user)


@router.patch("/settings", response_model=SettingsOut)
async def patch_settings(body: SettingsPatchIn, cu: CurrentUser = Depends(current_user),
                         s: SettingsService = Depends(svc)):
    return await s.patch(cu.user, body)


@router.post("/settings/delete-media", status_code=202)
async def delete_media(cu: CurrentUser = Depends(current_user), s: SettingsService = Depends(svc)):
    await s.delete_media(cu.user)
    return {"status": "scheduled"}


@router.get("/settings/group-invite-allowlist", response_model=list[UserPublicOut])
async def allowlist(cu: CurrentUser = Depends(current_user), s: SettingsService = Depends(svc)):
    return await s.allowlist(cu.user)


@router.post("/settings/group-invite-allowlist/{user_id}", status_code=204)
async def allow(user_id: str, cu: CurrentUser = Depends(current_user), s: SettingsService = Depends(svc)):
    await s.allow(cu.user, user_id)


@router.delete("/settings/group-invite-allowlist/{user_id}", status_code=204)
async def disallow(user_id: str, cu: CurrentUser = Depends(current_user), s: SettingsService = Depends(svc)):
    await s.disallow(cu.user, user_id)


@router.get("/blocklist", response_model=list[UserPublicOut])
async def blocklist(cu: CurrentUser = Depends(current_user), s: SettingsService = Depends(svc)):
    return await s.blocklist(cu.user)


@router.post("/blocklist/{user_id}", status_code=204)
async def block(user_id: str, cu: CurrentUser = Depends(current_user), s: SettingsService = Depends(svc)):
    await s.block(cu.user, user_id)


@router.delete("/blocklist/{user_id}", status_code=204)
async def unblock(user_id: str, cu: CurrentUser = Depends(current_user), s: SettingsService = Depends(svc)):
    await s.unblock(cu.user, user_id)
