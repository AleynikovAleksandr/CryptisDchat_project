"""10.1 Аутентификация и сессия + 10.10 Кошелёк."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, auth_rate_limit, client_ip, current_user, get_infra, get_session
from app.schemas.auth import (
    AccessOut,
    ChallengeOut,
    RefreshIn,
    SessionOut,
    TokenPairOut,
    TonProofVerifyIn,
    WalletDisconnectIn,
)
from app.schemas.users import MeOut
from app.services.auth_service import AuthService
from app.services.errors import NotFound
from app.services.infra import Infra

router = APIRouter(prefix="/api", tags=["auth"])

# ключ устройства: HttpOnly — недоступен JavaScript страницы; путь — только эндпоинты входа
DEVICE_COOKIE = "cx_device"
DEVICE_COOKIE_PATH = "/api/auth"


@router.post("/auth/ton-proof/challenge", response_model=ChallengeOut, dependencies=[Depends(auth_rate_limit)])
async def challenge(session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    return await AuthService(session, infra).challenge()


@router.post("/auth/ton-proof/verify", response_model=TokenPairOut, dependencies=[Depends(auth_rate_limit)])
async def verify(body: TonProofVerifyIn, request: Request, response: Response,
                 session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    svc = AuthService(session, infra)
    issued = await svc.login(body, request.headers.get("user-agent", ""), client_ip(request),
                             request.cookies.get(DEVICE_COOKIE))
    response.set_cookie(
        DEVICE_COOKIE, issued.device_key, max_age=infra.settings.device_cookie_max_age_days * 24 * 3600, path=DEVICE_COOKIE_PATH,
        httponly=True, samesite="strict", secure=not infra.settings.is_dev,
    )
    return TokenPairOut(
        access_token=issued.access_token, access_expires_at=issued.access_expires_at,
        refresh_token=issued.refresh_token, is_new_user=issued.is_new_user, user=await svc.me(issued.user),
    )


@router.post("/auth/refresh", response_model=AccessOut, dependencies=[Depends(auth_rate_limit)])
async def refresh(body: RefreshIn, session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    access, exp, new_refresh = await AuthService(session, infra).refresh(body.refresh_token)
    return AccessOut(access_token=access, access_expires_at=exp, refresh_token=new_refresh)


@router.post("/auth/logout", status_code=204)
async def logout(cu: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session),
                 infra: Infra = Depends(get_infra)):
    await AuthService(session, infra).logout(cu.user, cu.device_id)


@router.post("/auth/logout-all", status_code=204)
async def logout_all(cu: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session),
                     infra: Infra = Depends(get_infra)):
    await AuthService(session, infra).logout_all(cu.user)


@router.get("/auth/sessions", response_model=list[SessionOut])
async def sessions(cu: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session),
                   infra: Infra = Depends(get_infra)):
    return await AuthService(session, infra).sessions(cu.user, cu.device_id)


@router.delete("/auth/sessions/{device_id}", status_code=204)
async def end_session(device_id: str, cu: CurrentUser = Depends(current_user),
                      session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    """«Завершить сессию» на конкретном устройстве (ТЗ 4.2)."""
    svc = AuthService(session, infra)
    if device_id not in {s.device_id for s in await svc.sessions(cu.user, cu.device_id)}:
        raise NotFound("session not found")
    await svc.logout(cu.user, device_id)


@router.post("/wallet/connect", response_model=MeOut)
async def wallet_connect(body: TonProofVerifyIn, cu: CurrentUser = Depends(current_user),
                         session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    svc = AuthService(session, infra)
    await svc.link_wallet(cu.user, body)
    return await svc.me(cu.user)


@router.post("/wallet/disconnect", response_model=MeOut)
async def wallet_disconnect(body: WalletDisconnectIn, cu: CurrentUser = Depends(current_user),
                            session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)):
    svc = AuthService(session, infra)
    await svc.unlink_wallet(cu.user, body.address)
    return await svc.me(cu.user)
