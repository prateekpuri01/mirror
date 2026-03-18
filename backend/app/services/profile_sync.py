import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile import UserProfile

logger = logging.getLogger(__name__)


async def sync_profile_from_yaml(session: AsyncSession, yaml_path: str) -> UserProfile:
    """Read profile.yaml and upsert into user_profile table."""
    path = Path(yaml_path)
    if not path.exists():
        logger.warning("Profile YAML not found at %s — skipping sync", yaml_path)
        raise FileNotFoundError(f"Profile YAML not found: {yaml_path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(yaml_path=yaml_path, data=data, synced_at=datetime.now(timezone.utc))
        session.add(profile)
    else:
        profile.data = data
        profile.yaml_path = yaml_path
        profile.synced_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(profile)
    logger.info("Profile synced from %s at %s", yaml_path, profile.synced_at)
    return profile


async def sync_complete_profile(
    session: AsyncSession, complete_yaml_path: str
) -> UserProfile | None:
    """Read profile_complete.yaml and merge into user_profile.data['complete_profile']."""
    path = Path(complete_yaml_path)
    if not path.exists():
        logger.info("Complete profile YAML not found at %s — skipping", complete_yaml_path)
        return None

    with open(path) as f:
        complete_data = yaml.safe_load(f)

    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        logger.warning("Base profile not synced yet — cannot attach complete profile")
        return None

    # Merge complete profile into the data JSONB
    updated = dict(profile.data) if profile.data else {}
    updated["complete_profile"] = complete_data
    profile.data = updated
    profile.synced_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(profile)
    logger.info("Complete profile synced from %s", complete_yaml_path)
    return profile
