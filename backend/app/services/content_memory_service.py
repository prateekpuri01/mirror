"""CRUD + lookup for ``content_memory``.

The write path takes a resume edit (dotted path + post-edit content_json) and
upserts a single row keyed on ``(entity_type, entity_key, source_doc_id)``.
The read path (``fetch_grounding``) returns recent past versions for a set of
entity keys, used to assemble the grounding block injected into generation
prompts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.content_memory_paths import (
    EXPERIENCE_BULLETS_SET,
    RESEARCH_DESCRIPTION,
    path_to_entity,
)
from app.models import ContentMemory, Document, Job

logger = logging.getLogger(__name__)


# How many past versions to surface per entity at generation time. The
# grounding block trims further if the budget is tight, but this caps the
# query.
DEFAULT_PER_ENTITY_LIMIT = 3


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


async def upsert_from_edit(
    session: AsyncSession,
    *,
    doc_id: uuid.UUID,
    path: str,
    content_json: dict,
    profile_data: dict | None,
) -> ContentMemory | None:
    """Translate one inline edit into a content_memory upsert.

    Returns the persisted row, or None if the path isn't a memorized entity
    or the payload is empty / malformed. Callers should treat None as "no-op".
    """
    descriptor = path_to_entity(path, content_json)
    if descriptor is None:
        return None
    if descriptor.get("user_text") in (None, "") and descriptor.get("user_payload_json") in (
        None,
        [],
    ):
        # Nothing to memorize.
        return None

    doc = await session.get(Document, doc_id)
    if doc is None:
        logger.warning("content_memory: document %s not found, skipping upsert", doc_id)
        return None

    job_context = await _build_job_context(session, doc.job_id)
    source_text_hash = _hash_source_text(
        descriptor["entity_type"], descriptor["accomplishment_ids"], profile_data
    )

    values = {
        "id": uuid.uuid4(),
        "entity_type": descriptor["entity_type"],
        "entity_key": descriptor["entity_key"],
        "source_doc_id": doc_id,
        "source_job_id": doc.job_id,
        "user_text": descriptor["user_text"],
        "user_payload_json": descriptor["user_payload_json"],
        "job_context": job_context,
        "source_text_hash": source_text_hash,
        "is_active": True,
    }

    stmt = (
        pg_insert(ContentMemory)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_content_memory_entity_doc",
            set_={
                "user_text": values["user_text"],
                "user_payload_json": values["user_payload_json"],
                "job_context": values["job_context"],
                "source_text_hash": values["source_text_hash"],
                "is_active": True,
                "source_job_id": values["source_job_id"],
                "updated_at": _now_func(),
            },
        )
        .returning(ContentMemory.id)
    )
    result = await session.execute(stmt)
    new_id = result.scalar_one()

    row = await session.get(ContentMemory, new_id)
    logger.info(
        "content_memory upsert: type=%s key=%s doc=%s",
        descriptor["entity_type"],
        descriptor["entity_key"],
        doc_id,
    )
    return row


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


async def fetch_section_history(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_key: str,
    exclude_doc_id: uuid.UUID | None = None,
    limit: int = 25,
) -> list[ContentMemory]:
    """Past versions for a single entity, newest first. Excludes the current doc
    so the editor's dropdown doesn't list the version the user is already viewing.
    """
    query = (
        select(ContentMemory)
        .where(
            ContentMemory.entity_type == entity_type,
            ContentMemory.entity_key == entity_key,
            ContentMemory.is_active.is_(True),
        )
        .order_by(ContentMemory.updated_at.desc())
        .limit(limit)
    )
    if exclude_doc_id is not None:
        query = query.where(ContentMemory.source_doc_id != exclude_doc_id)
    result = await session.execute(query)
    return list(result.scalars().all())


async def fetch_grounding(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_keys: Iterable[str] | None = None,
    per_entity_limit: int = DEFAULT_PER_ENTITY_LIMIT,
) -> dict[str, list[ContentMemory]]:
    """Return active rows grouped by entity_key, ordered newest-first.

    For scalar entities (summary, tagline) pass ``entity_keys=None`` and the
    sentinel key is used implicitly. For other types pass the keys you care
    about — typically the accomplishment_ids the planner picked, or the set
    of employer keys in the user's profile.
    """
    query = (
        select(ContentMemory)
        .where(
            ContentMemory.entity_type == entity_type,
            ContentMemory.is_active.is_(True),
        )
        .order_by(ContentMemory.updated_at.desc())
    )
    keys_list: list[str] | None = None
    if entity_keys is not None:
        keys_list = [k for k in entity_keys if k]
        if not keys_list:
            return {}
        query = query.where(ContentMemory.entity_key.in_(keys_list))

    result = await session.execute(query)
    rows = list(result.scalars().all())

    grouped: dict[str, list[ContentMemory]] = {}
    for row in rows:
        bucket = grouped.setdefault(row.entity_key, [])
        if len(bucket) < per_entity_limit:
            bucket.append(row)
    return grouped


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------


def _hash_source_text(
    entity_type: str,
    accomplishment_ids: list[str],
    profile_data: dict | None,
) -> str | None:
    """Compute sha256 of the underlying accomplishment text(s) at save time.

    Returns None for entities that don't reference accomplishments (skill
    buckets, summary, tagline) or when the profile isn't available.
    """
    if not accomplishment_ids or profile_data is None:
        return None
    if entity_type not in (RESEARCH_DESCRIPTION, EXPERIENCE_BULLETS_SET):
        return None

    accomplishments = (profile_data.get("complete_profile") or {}).get("accomplishments") or []
    by_id: dict[str, dict] = {a.get("id"): a for a in accomplishments if a.get("id")}

    # Build a stable, accomplishment-id-sorted blob so reorders don't change
    # the hash.
    blob_parts: list[str] = []
    for aid in sorted(set(accomplishment_ids)):
        a = by_id.get(aid)
        if a is None:
            blob_parts.append(f"{aid}::missing")
            continue
        relevant = {
            "id": aid,
            "title": a.get("title", ""),
            "impact_summary": a.get("impact_summary", ""),
            "quantitative_specifics": a.get("quantitative_specifics", []),
            "so_what": a.get("so_what", ""),
            "hands_on_work": a.get("hands_on_work", ""),
        }
        blob_parts.append(json.dumps(relevant, sort_keys=True))
    blob = "\n".join(blob_parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_source_hash(
    entity_type: str,
    accomplishment_ids: list[str],
    profile_data: dict | None,
) -> str | None:
    """Public helper for callers that need to recompute the hash at lookup."""
    return _hash_source_text(entity_type, accomplishment_ids, profile_data)


def is_stale(row: ContentMemory, profile_data: dict | None) -> bool:
    """Return True if the row's stored source hash no longer matches current
    profile data. Returns False if the row didn't store a hash (we can't tell)."""
    if row.source_text_hash is None:
        return False
    accomplishment_ids = _accomplishment_ids_from_row(row)
    current = _hash_source_text(row.entity_type, accomplishment_ids, profile_data)
    if current is None:
        return False
    return current != row.source_text_hash


def _accomplishment_ids_from_row(row: ContentMemory) -> list[str]:
    """Extract the accomplishment_ids referenced by a stored row.

    For research_description: a single id taken from ``entity_key``.
    For experience_bullets_set: the union of ids across all bullets in the
    payload.
    """
    if row.entity_type == RESEARCH_DESCRIPTION:
        return [row.entity_key] if row.entity_key else []
    if row.entity_type == EXPERIENCE_BULLETS_SET:
        bullets = row.user_payload_json or []
        seen: set[str] = set()
        out: list[str] = []
        for b in bullets:
            if isinstance(b, dict):
                for aid in b.get("accomplishment_ids") or []:
                    if isinstance(aid, str) and aid not in seen:
                        seen.add(aid)
                        out.append(aid)
        return out
    return []


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _build_job_context(session: AsyncSession, job_id: uuid.UUID | None) -> dict | None:
    """Capture a small JSON blob describing the job an edit was made for."""
    if job_id is None:
        return None
    job = await session.get(Job, job_id)
    if job is None:
        return None
    score_excerpt: str | None = None
    if job.score_rationale:
        # Keep a compact excerpt — enough for the LLM to pattern-match the
        # role family without ballooning the grounding block.
        try:
            role_fit = (job.score_rationale or {}).get("role_fit", {})
            interest = (job.score_rationale or {}).get("interest_fit", {})
            score_excerpt = json.dumps(
                {"role_fit": role_fit, "interest_fit": interest},
                separators=(",", ":"),
            )[:400]
        except Exception:
            score_excerpt = None
    return {
        "job_id": str(job_id),
        "job_title": job.title or "",
        "company_name": job.company or "",
        "score_excerpt": score_excerpt,
    }


def _now_func():
    """Return the SQL ``now()`` function expression for use in update sets."""
    from sqlalchemy import func

    return func.now()
