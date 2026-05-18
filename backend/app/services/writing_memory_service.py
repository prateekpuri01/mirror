"""CRUD and lifecycle operations for the writing memory system."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.writing_memory import WritingMemory

# Confidence boost per reinforcement observation
_REINFORCE_DELTA = 0.15
# Rules below this confidence are excluded from prompt injection
MIN_INJECTION_CONFIDENCE = 0.6
# Max active rules per domain
MAX_RULES_PER_DOMAIN = 30
# Rules older than this without reinforcement start decaying
DECAY_THRESHOLD_DAYS = 90
# Confidence drop per decay cycle
DECAY_AMOUNT = 0.1
# Rules that decay below this are auto-deactivated
DECAY_FLOOR = 0.3


async def get_active_rules(
    session: AsyncSession,
    domain: str,
    min_confidence: float = MIN_INJECTION_CONFIDENCE,
) -> list[WritingMemory]:
    """Return active universal rules above the confidence threshold, ordered by confidence DESC."""
    result = await session.execute(
        select(WritingMemory)
        .where(
            WritingMemory.domain == domain,
            WritingMemory.is_active.is_(True),
            WritingMemory.scope == "universal",
            WritingMemory.confidence >= min_confidence,
        )
        .order_by(WritingMemory.confidence.desc())
    )
    return list(result.scalars().all())


async def get_all_rules(
    session: AsyncSession,
    domain: str | None = None,
    scope: str | None = None,
    is_active: bool | None = None,
) -> list[WritingMemory]:
    """Return rules with optional filters, for management UI."""
    query = select(WritingMemory).order_by(WritingMemory.updated_at.desc())
    if domain is not None:
        query = query.where(WritingMemory.domain == domain)
    if scope is not None:
        query = query.where(WritingMemory.scope == scope)
    if is_active is not None:
        query = query.where(WritingMemory.is_active.is_(is_active))
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_rule(session: AsyncSession, rule_id: uuid.UUID) -> WritingMemory | None:
    result = await session.execute(
        select(WritingMemory).where(WritingMemory.id == rule_id)
    )
    return result.scalar_one_or_none()


async def add_rule(
    session: AsyncSession,
    *,
    domain: str,
    rule_text: str,
    category: str,
    scope: str = "universal",
    examples_json: list[dict] | None = None,
    source_type: str,
    source_job_id: uuid.UUID | None = None,
    confidence: float | None = None,
) -> WritingMemory:
    """Create a new rule. Confidence defaults based on source_type."""
    if confidence is None:
        confidence = 0.8 if source_type == "explicit_user" else 0.5

    rule = WritingMemory(
        domain=domain,
        rule_text=rule_text,
        category=category,
        scope=scope,
        examples_json=examples_json,
        source_type=source_type,
        source_job_id=source_job_id,
        confidence=confidence,
        occurrence_count=1,
        is_active=True,
    )
    session.add(rule)
    await session.flush()

    # Enforce per-domain cap
    await enforce_cap(session, domain)

    return rule


async def reinforce_rule(session: AsyncSession, rule_id: uuid.UUID) -> WritingMemory | None:
    """Increment occurrence count and boost confidence."""
    rule = await get_rule(session, rule_id)
    if rule is None:
        return None
    rule.occurrence_count += 1
    rule.confidence = min(1.0, rule.confidence + _REINFORCE_DELTA)
    rule.is_active = True  # re-activate if it was decayed
    await session.flush()
    return rule


async def deactivate_rule(session: AsyncSession, rule_id: uuid.UUID) -> WritingMemory | None:
    rule = await get_rule(session, rule_id)
    if rule is None:
        return None
    rule.is_active = False
    await session.flush()
    return rule


async def update_rule(
    session: AsyncSession,
    rule_id: uuid.UUID,
    **kwargs,
) -> WritingMemory | None:
    """Partial update. Accepted keys: rule_text, category, scope, is_active."""
    rule = await get_rule(session, rule_id)
    if rule is None:
        return None
    allowed = {"rule_text", "category", "scope", "is_active"}
    for key, value in kwargs.items():
        if key in allowed and value is not None:
            setattr(rule, key, value)
    await session.flush()
    return rule


async def enforce_cap(session: AsyncSession, domain: str, max_rules: int = MAX_RULES_PER_DOMAIN) -> int:
    """Deactivate lowest-confidence rules if over the cap. Returns count deactivated."""
    count_result = await session.execute(
        select(func.count())
        .select_from(WritingMemory)
        .where(WritingMemory.domain == domain, WritingMemory.is_active.is_(True))
    )
    total = count_result.scalar_one()
    if total <= max_rules:
        return 0

    # Find the IDs to deactivate (lowest confidence first)
    excess = total - max_rules
    to_deactivate = await session.execute(
        select(WritingMemory.id)
        .where(WritingMemory.domain == domain, WritingMemory.is_active.is_(True))
        .order_by(WritingMemory.confidence.asc(), WritingMemory.updated_at.asc())
        .limit(excess)
    )
    ids = [row[0] for row in to_deactivate.all()]
    if ids:
        await session.execute(
            update(WritingMemory)
            .where(WritingMemory.id.in_(ids))
            .values(is_active=False)
        )
        await session.flush()
    return len(ids)


async def decay_stale_rules(session: AsyncSession) -> tuple[int, int]:
    """Decay confidence on rules not reinforced recently.

    Returns (decayed_count, deactivated_count).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=DECAY_THRESHOLD_DAYS)

    # Find stale active rules
    result = await session.execute(
        select(WritingMemory).where(
            WritingMemory.is_active.is_(True),
            WritingMemory.updated_at < cutoff,
        )
    )
    stale_rules = list(result.scalars().all())
    decayed = 0
    deactivated = 0
    for rule in stale_rules:
        rule.confidence = round(rule.confidence - DECAY_AMOUNT, 2)
        decayed += 1
        if rule.confidence < DECAY_FLOOR:
            rule.is_active = False
            deactivated += 1
    if stale_rules:
        await session.flush()
    return decayed, deactivated
