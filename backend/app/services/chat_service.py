import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatActionCard, ChatMessage


async def list_messages(session: AsyncSession, job_id: uuid.UUID) -> list[ChatMessage]:
    """Return all chat messages for a job, ordered oldest-first."""
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.job_id == job_id)
        .order_by(ChatMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def add_message(
    session: AsyncSession,
    job_id: uuid.UUID,
    role: str,
    content: str,
    section_context: str | None = None,
) -> ChatMessage:
    """Persist a single chat message."""
    msg = ChatMessage(
        job_id=job_id,
        role=role,
        content=content,
        section_context=section_context,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


async def clear_chat(session: AsyncSession, job_id: uuid.UUID) -> int:
    """Delete all chat messages for a job. Returns count deleted."""
    result = await session.execute(delete(ChatMessage).where(ChatMessage.job_id == job_id))
    await session.commit()
    return result.rowcount


# ---------------------------------------------------------------------------
# Action cards (brainstorm-mode edit suggestions)
# ---------------------------------------------------------------------------


async def persist_action_cards(
    session: AsyncSession,
    job_id: uuid.UUID,
    message_id: uuid.UUID,
    raw_cards: list[dict],
) -> list[ChatActionCard]:
    """Persist a list of action cards attached to an assistant message.

    `raw_cards` is the parsed list emitted by the brainstorm handler — each
    is a dict with keys: kind, section_path?, rationale?, proposed_value, ...
    Returns the persisted rows (with assigned UUIDs), in card_index order.
    """
    rows: list[ChatActionCard] = []
    for idx, card in enumerate(raw_cards):
        known_keys = {"kind", "section_path", "rationale", "proposed_value"}
        extra = {k: v for k, v in card.items() if k not in known_keys}
        proposed = card.get("proposed_value")
        if not isinstance(proposed, str):
            proposed = str(proposed) if proposed is not None else ""
        row = ChatActionCard(
            message_id=message_id,
            job_id=job_id,
            card_index=idx,
            kind=str(card.get("kind") or ""),
            section_path=card.get("section_path") or None,
            rationale=card.get("rationale") or None,
            proposed_value=proposed,
            extra=extra or None,
        )
        session.add(row)
        rows.append(row)
    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows


async def list_action_cards_for_job(
    session: AsyncSession, job_id: uuid.UUID
) -> list[ChatActionCard]:
    """All action cards for a job, oldest-first (matches chat order)."""
    result = await session.execute(
        select(ChatActionCard)
        .where(ChatActionCard.job_id == job_id)
        .order_by(ChatActionCard.created_at.asc())
    )
    return list(result.scalars().all())


async def get_action_card(session: AsyncSession, card_id: uuid.UUID) -> ChatActionCard | None:
    result = await session.execute(select(ChatActionCard).where(ChatActionCard.id == card_id))
    return result.scalar_one_or_none()


async def mark_action_card(
    session: AsyncSession, card_id: uuid.UUID, status: str
) -> ChatActionCard | None:
    """Flip a card's status to 'applied' or 'dismissed' and stamp resolved_at."""
    card = await get_action_card(session, card_id)
    if card is None:
        return None
    card.status = status
    card.resolved_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(card)
    return card
