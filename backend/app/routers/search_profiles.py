import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.search_profiles import (
    SearchProfileCreate,
    SearchProfileRead,
    SearchProfileUpdate,
)
from app.services import search_profile_service

router = APIRouter(prefix="/api", tags=["search-profiles"])


@router.get("/search-profiles", response_model=list[SearchProfileRead])
async def list_profiles(session: AsyncSession = Depends(get_session)):
    return await search_profile_service.list_search_profiles(session)


@router.post("/search-profiles", response_model=SearchProfileRead, status_code=201)
async def create_profile(body: SearchProfileCreate, session: AsyncSession = Depends(get_session)):
    profile = await search_profile_service.create_search_profile(session, body.model_dump())
    return profile


@router.patch("/search-profiles/{profile_id}", response_model=SearchProfileRead)
async def update_profile(
    profile_id: uuid.UUID,
    body: SearchProfileUpdate,
    session: AsyncSession = Depends(get_session),
):
    data = body.model_dump(exclude_unset=True)
    profile = await search_profile_service.update_search_profile(session, profile_id, data)
    if profile is None:
        raise HTTPException(status_code=404, detail="Search profile not found")
    return profile


@router.delete("/search-profiles/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    deleted = await search_profile_service.delete_search_profile(session, profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Search profile not found")
