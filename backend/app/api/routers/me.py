"""10.2 Профиль, 10.3 Каталог пользователей, push-токены и жалобы."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, current_user, get_infra, get_session
from app.repositories import SettingsRepository, UserRepository
from app.schemas.users import MePatchIn, MeOut, PushTokenIn, ReportIn, UserPublicOut
from app.services import cache
from app.services.auth_service import AuthService
from app.services.errors import NotFound
from app.services.infra import Infra
from app.services.message_service import MessageService, update_profile
from app.services.presenters import user_public
from app.services.settings_service import SettingsService

router = APIRouter(prefix="/api", tags=["profile"])


@router.get("/me", response_model=MeOut)
async def get_me(cu: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session),
                 infra: Infra = Depends(get_infra)):
    return await AuthService(session, infra).me(cu.user)


@router.patch("/me", response_model=MeOut)
async def patch_me(body: MePatchIn, cu: CurrentUser = Depends(current_user),
                   session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    await update_profile(session, infra, cu.user, display_name=body.display_name,
                         username=body.username, bio=body.bio)
    return await AuthService(session, infra).me(cu.user)


@router.post("/me/avatar", response_model=MeOut)
async def upload_avatar(file: UploadFile = File(...), cu: CurrentUser = Depends(current_user),
                        session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    svc = MessageService(session, infra)
    data = await file.read(2 * 1024 * 1024 + 1)
    uploaded = await svc.upload(cu.user, data, "avatar", file.content_type or "")
    await svc.set_my_avatar(cu.user, uploaded.id)
    return await AuthService(session, infra).me(cu.user)


@router.post("/me/push-token", status_code=204)
async def push_token(body: PushTokenIn, cu: CurrentUser = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    await UserRepository(session).upsert_push_token(cu.user.id, cu.device_id, body.platform, body.token)
    await session.commit()


@router.get("/contacts", response_model=list[UserPublicOut])
async def contacts(query: str = "", cu: CurrentUser = Depends(current_user),
                   session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    repo = UserRepository(session)
    found = await repo.search(query[:80], exclude_id=cu.user.id)
    ids = [u.id for u in found]
    wallets = await repo.primary_wallets(ids)
    prefs = await SettingsRepository(session).get_many(ids)
    online = await cache.online_users(infra.redis, [i for i in ids if prefs[i].show_online])
    return [user_public(u, wallets.get(u.id), online=u.id in online) for u in found]


@router.get("/users/{user_id}", response_model=UserPublicOut)
async def user_profile(user_id: str, cu: CurrentUser = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    repo = UserRepository(session)
    user = await repo.get(user_id)
    if user is None:
        raise NotFound("user not found")
    return user_public(user, (await repo.primary_wallets([user_id])).get(user_id))


@router.get("/media/avatars/{upload_id}")
async def avatar(upload_id: str, session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    """Аватар — публичная часть профиля; URL содержит неугадываемый UUID, поэтому отдаётся
    без Bearer-токена (его нельзя передать из CSS background-image)."""
    data, content_type = await MessageService(session, infra).avatar(upload_id)
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.post("/reports", status_code=201)
async def create_report(body: ReportIn, cu: CurrentUser = Depends(current_user),
                        session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    return {"id": await SettingsService(session, infra).report(cu.user, body)}
