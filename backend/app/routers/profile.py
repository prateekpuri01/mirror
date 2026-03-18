from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.profile import UserProfile

router = APIRouter(prefix="/api", tags=["profile"])


@router.get("/profile")
async def get_profile(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not synced yet")
    # Return base profile data without complete_profile to keep response small
    data = dict(profile.data)
    data.pop("complete_profile", None)
    return data


@router.get("/profile/complete")
async def get_complete_profile(session: AsyncSession = Depends(get_session)):
    """Return the full comprehensive profile with all accomplishments and publications."""
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not synced yet")
    complete = profile.data.get("complete_profile")
    if complete is None:
        raise HTTPException(
            status_code=404, detail="Complete profile not synced yet"
        )
    return complete
