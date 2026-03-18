import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApplicationRequirements


async def get_for_job(session: AsyncSession, job_id: uuid.UUID) -> ApplicationRequirements | None:
    result = await session.execute(
        select(ApplicationRequirements).where(ApplicationRequirements.job_id == job_id)
    )
    return result.scalar_one_or_none()


async def upsert(
    session: AsyncSession, job_id: uuid.UUID, data: dict
) -> ApplicationRequirements:
    existing = await get_for_job(session, job_id)
    if existing is None:
        req = ApplicationRequirements(job_id=job_id, **data)
        session.add(req)
    else:
        for key, value in data.items():
            setattr(existing, key, value)
        req = existing
    await session.commit()
    await session.refresh(req)
    return req
