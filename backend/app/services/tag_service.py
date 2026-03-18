import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tag


async def list_tags(session: AsyncSession) -> list[Tag]:
    result = await session.execute(select(Tag).order_by(Tag.name))
    return list(result.scalars().all())


async def create_tag(session: AsyncSession, data: dict) -> Tag:
    tag = Tag(**data)
    session.add(tag)
    await session.commit()
    await session.refresh(tag)
    return tag


async def delete_tag(session: AsyncSession, tag_id: uuid.UUID) -> bool:
    result = await session.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if tag is None:
        return False
    await session.delete(tag)
    await session.commit()
    return True
