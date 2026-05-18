import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.profile import UserProfile

logger = logging.getLogger(__name__)


def _slugify(s: str, max_words: int = 2) -> str:
    """Lowercase, replace non-alphanum with hyphen, take leading words."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", s or "").strip("-").lower()
    if not cleaned:
        return ""
    parts = [p for p in cleaned.split("-") if p][:max_words]
    return "-".join(parts)


def _employer_prefix(employer: str) -> str:
    """Short stable prefix from an employer name. 'The RAND Corporation' -> 'rand'."""
    if not employer:
        return ""
    cleaned = re.sub(r"^the\s+", "", employer.strip(), flags=re.IGNORECASE)
    first_word = re.split(r"\s+", cleaned)[0]
    return re.sub(r"[^a-zA-Z0-9]+", "", first_word).lower()


def _ensure_accomplishment_ids(accomplishments: list[dict]) -> list[dict]:
    """Assign stable kebab-case ids to accomplishments missing one.

    Format: ``{employer-prefix}-{title-slug}`` (e.g. ``rand-habitus``).
    Falls back to ``acc-{n}`` when employer and title are both empty.
    Dedupes against existing ids by appending ``-2``, ``-3``, …

    Called from ``update_complete_profile`` so accomplishments added via
    the Profile UI (which doesn't ask the user for an ID) are immediately
    eligible for the resume-editor's "swap research" dropdown — which
    filters on ``a.id`` and silently drops entries without one.
    """
    existing_ids: set[str] = {a.get("id") for a in accomplishments if a.get("id")}
    next_fallback = 1
    for acc in accomplishments:
        if acc.get("id"):
            continue
        prefix = _employer_prefix(acc.get("employer", "") or "")
        title_slug = _slugify(acc.get("title", "") or "")
        if prefix and title_slug:
            base_id = f"{prefix}-{title_slug}"
        elif title_slug:
            base_id = title_slug
        elif prefix:
            base_id = f"{prefix}-{next_fallback}"
            next_fallback += 1
        else:
            base_id = f"acc-{next_fallback}"
            next_fallback += 1

        candidate = base_id
        counter = 2
        while candidate in existing_ids:
            candidate = f"{base_id}-{counter}"
            counter += 1
        acc["id"] = candidate
        existing_ids.add(candidate)
        logger.info("Assigned accomplishment id %r to %r", candidate, acc.get("title", "")[:60])
    return accomplishments


async def sync_profile_from_yaml(session: AsyncSession, yaml_path: str) -> UserProfile:
    """Read profile.yaml and upsert into user_profile table.

    Only inserts if no profile exists yet — never overwrites UI edits.
    """
    path = Path(yaml_path)
    if not path.exists():
        logger.warning("Profile YAML not found at %s — skipping sync", yaml_path)
        raise FileNotFoundError(f"Profile YAML not found: {yaml_path}")

    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    # If a profile already exists in DB, skip YAML sync to preserve UI edits
    if profile is not None:
        logger.info("Profile already exists in DB — skipping YAML sync to preserve edits")
        return profile

    with open(path) as f:
        data = yaml.safe_load(f)

    profile = UserProfile(yaml_path=yaml_path, data=data, synced_at=datetime.now(timezone.utc))
    session.add(profile)

    await session.commit()
    await session.refresh(profile)
    logger.info("Profile synced from %s at %s", yaml_path, profile.synced_at)
    return profile


async def sync_complete_profile(
    session: AsyncSession, complete_yaml_path: str
) -> UserProfile | None:
    """Read profile_complete.yaml and merge into user_profile.data['complete_profile'].

    Only merges if complete_profile doesn't already exist in DB.
    """
    path = Path(complete_yaml_path)
    if not path.exists():
        logger.info("Complete profile YAML not found at %s — skipping", complete_yaml_path)
        return None

    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        logger.warning("Base profile not synced yet — cannot attach complete profile")
        return None

    # If complete_profile already exists, skip to preserve UI edits
    if profile.data and profile.data.get("complete_profile"):
        logger.info("Complete profile already in DB — skipping YAML sync to preserve edits")
        return profile

    with open(path) as f:
        complete_data = yaml.safe_load(f)

    # Merge complete profile into the data JSONB
    updated = dict(profile.data) if profile.data else {}
    updated["complete_profile"] = complete_data
    profile.data = updated
    profile.synced_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(profile)
    logger.info("Complete profile synced from %s", complete_yaml_path)
    return profile


async def update_profile_section(
    session: AsyncSession, sections: dict
) -> dict:
    """Merge top-level sections into profile.data JSONB.

    Args:
        sections: dict of {section_name: section_data} to merge.
                  e.g. {"personal": {...}, "skills": {...}}

    Returns the full updated profile data (without complete_profile).
    """
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        # Create a new profile with the provided sections
        profile = UserProfile(
            yaml_path="",
            data=sections,
            synced_at=datetime.now(timezone.utc),
        )
        session.add(profile)
    else:
        updated = dict(profile.data) if profile.data else {}
        for key, value in sections.items():
            if key == "complete_profile":
                continue  # Don't allow complete_profile via this endpoint
            updated[key] = value
        profile.data = updated
        profile.synced_at = datetime.now(timezone.utc)

    await session.commit()
    await session.refresh(profile)

    # Return without complete_profile
    data = dict(profile.data)
    data.pop("complete_profile", None)
    return data


async def update_complete_profile(
    session: AsyncSession, sections: dict
) -> dict:
    """Merge sections into profile.data['complete_profile'].

    Args:
        sections: dict of {section_name: section_data} to merge.
                  e.g. {"accomplishments": [...], "publications": [...]}

    Returns the updated complete_profile dict.
    """
    result = await session.execute(select(UserProfile).limit(1))
    profile = result.scalar_one_or_none()

    if profile is None:
        raise ValueError("No profile exists yet — create a base profile first")

    updated = dict(profile.data) if profile.data else {}
    complete = dict(updated.get("complete_profile", {}))
    for key, value in sections.items():
        complete[key] = value
    # Auto-assign stable IDs to accomplishments added via the Profile UI.
    # The UI's "Add accomplishment" handler doesn't ask the user for an
    # id, so without this the resume-editor's swap-research dropdown
    # silently drops them (it filters on ``a.id``).
    if isinstance(complete.get("accomplishments"), list):
        complete["accomplishments"] = _ensure_accomplishment_ids(complete["accomplishments"])
    updated["complete_profile"] = complete
    profile.data = updated
    profile.synced_at = datetime.now(timezone.utc)
    # SQLAlchemy doesn't reliably detect nested-JSONB mutations even when
    # the top-level attribute is reassigned (it can do an equality check
    # that sees no change when the nested list is shared between old and
    # new dicts). Force the column dirty so the update sticks.
    flag_modified(profile, "data")

    await session.commit()
    await session.refresh(profile)
    return profile.data.get("complete_profile", {})
