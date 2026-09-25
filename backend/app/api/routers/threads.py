"""10.4 Треды и 10.8 Настройки уровня чата."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, current_user, get_infra, get_session
from app.schemas.threads import (
    DirectThreadIn,
    DisappearingIn,
    MuteIn,
    ReadIn,
    ScreenshotBlockIn,
    ThreadDetailOut,
    ThreadOut,
)
from app.services.infra import Infra
from app.services.thread_service import ThreadService

router = APIRouter(prefix="/api/threads", tags=["threads"])


def svc(session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)) -> ThreadService:
    return ThreadService(session, infra)


@router.get("", response_model=list[ThreadOut])
async def list_threads(filter: str = "all", query: str = "", cu: CurrentUser = Depends(current_user),  # noqa: A002
                       s: ThreadService = Depends(svc)):
    return await s.list(cu.user, filter, query)


@router.post("", response_model=ThreadDetailOut)
async def open_direct(body: DirectThreadIn, cu: CurrentUser = Depends(current_user), s: ThreadService = Depends(svc)):
    return await s.open_direct(cu.user, body.user_id)


@router.get("/{thread_id}", response_model=ThreadDetailOut)
async def get_thread(thread_id: str, cu: CurrentUser = Depends(current_user), s: ThreadService = Depends(svc)):
    return await s.detail(cu.user, thread_id)


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, cu: CurrentUser = Depends(current_user), s: ThreadService = Depends(svc)):
    await s.hide(cu.user, thread_id)


@router.post("/{thread_id}/clear", status_code=204)
async def clear_thread(thread_id: str, cu: CurrentUser = Depends(current_user), s: ThreadService = Depends(svc)):
    await s.clear(cu.user, thread_id)


@router.post("/{thread_id}/mute", status_code=204)
async def mute_thread(thread_id: str, body: MuteIn, cu: CurrentUser = Depends(current_user),
                      s: ThreadService = Depends(svc)):
    await s.mute(cu.user, thread_id, body.muted)


@router.post("/{thread_id}/read")
async def read_thread(thread_id: str, body: ReadIn | None = None, cu: CurrentUser = Depends(current_user),
                      s: ThreadService = Depends(svc)):
    return {"last_read_seq": await s.mark_read(cu.user, thread_id, body.up_to_seq if body else None)}


@router.patch("/{thread_id}/disappearing", status_code=204)
async def disappearing(thread_id: str, body: DisappearingIn, cu: CurrentUser = Depends(current_user),
                       s: ThreadService = Depends(svc)):
    await s.set_disappearing(cu.user, thread_id, body.seconds)


@router.patch("/{thread_id}/screenshot-block", status_code=204)
async def screenshot_block(thread_id: str, body: ScreenshotBlockIn, cu: CurrentUser = Depends(current_user),
                           s: ThreadService = Depends(svc)):
    await s.set_screenshot_block(cu.user, thread_id, body.enabled)
