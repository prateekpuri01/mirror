import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SearchProfile


async def list_search_profiles(session: AsyncSession) -> list[SearchProfile]:
    result = await session.execute(select(SearchProfile).order_by(SearchProfile.created_at.desc()))
    return list(result.scalars().all())


async def create_search_profile(session: AsyncSession, data: dict) -> SearchProfile:
    profile = SearchProfile(**data)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


async def update_search_profile(
    session: AsyncSession, profile_id: uuid.UUID, data: dict
) -> SearchProfile | None:
    result = await session.execute(select(SearchProfile).where(SearchProfile.id == profile_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        return None
    for key, value in data.items():
        setattr(profile, key, value)
    await session.commit()
    await session.refresh(profile)
    return profile


async def delete_search_profile(session: AsyncSession, profile_id: uuid.UUID) -> bool:
    result = await session.execute(select(SearchProfile).where(SearchProfile.id == profile_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        return False
    await session.delete(profile)
    await session.commit()
    return True
