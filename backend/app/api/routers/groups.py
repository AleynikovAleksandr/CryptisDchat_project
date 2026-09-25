"""10.5 Группы."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUser, current_user, get_infra, get_session
from app.schemas.threads import AddMembersIn, GroupCreateIn, GroupPatchIn, MemberOut, ThreadDetailOut
from app.services.group_service import GroupService
from app.services.infra import Infra

router = APIRouter(prefix="/api/groups", tags=["groups"])


def svc(session: AsyncSession = Depends(get_session), infra: Infra = Depends(get_infra)) -> GroupService:
    return GroupService(session, infra)


@router.post("", response_model=ThreadDetailOut, status_code=201)
async def create_group(body: GroupCreateIn, cu: CurrentUser = Depends(current_user), s: GroupService = Depends(svc)):
    return await s.create(cu.user, body)


@router.get("/{group_id}/members", response_model=list[MemberOut])
async def members(group_id: str, cu: CurrentUser = Depends(current_user), s: GroupService = Depends(svc)):
    return await s.members(cu.user, group_id)


@router.post("/{group_id}/members", response_model=list[MemberOut])
async def add_members(group_id: str, body: AddMembersIn, cu: CurrentUser = Depends(current_user),
                      s: GroupService = Depends(svc)):
    return await s.add_members(cu.user, group_id, body.user_ids)


@router.delete("/{group_id}/members/{user_id}", status_code=204)
async def remove_member(group_id: str, user_id: str, cu: CurrentUser = Depends(current_user),
                        s: GroupService = Depends(svc)):
    await s.remove_member(cu.user, group_id, user_id)


@router.patch("/{group_id}", response_model=ThreadDetailOut)
async def patch_group(group_id: str, body: GroupPatchIn, cu: CurrentUser = Depends(current_user),
                      s: GroupService = Depends(svc)):
    return await s.patch(cu.user, group_id, body)
