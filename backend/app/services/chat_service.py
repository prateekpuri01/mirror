import uuid

from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage


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
    result = await session.execute(
        delete(ChatMessage).where(ChatMessage.job_id == job_id)
    )
    await session.commit()
    return result.rowcount
