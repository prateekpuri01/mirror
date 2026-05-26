import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_session
from app.schemas.tags import TagCreate, TagRead
from app.services import tag_service
from app.services.auto_tagger import apply_tag_to_all_jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["tags"])


@router.get("/tags", response_model=list[TagRead])
async def list_tags(session: AsyncSession = Depends(get_session)):
    return await tag_service.list_tags(session)


async def _backfill_tag(tag_id: uuid.UUID) -> None:
    """Background task: apply a newly created tag to all existing jobs."""
    try:
        async with async_session() as session:
            from sqlalchemy import select

            from app.models import Tag

            result = await session.execute(select(Tag).where(Tag.id == tag_id))
            tag = result.scalar_one_or_none()
            if tag:
                stats = await apply_tag_to_all_jobs(session, tag)
                logger.info("Backfilled tag '%s' to %d jobs", tag.name, stats["tagged_jobs"])
    except Exception:
        logger.exception("Tag backfill failed for %s", tag_id)


@router.post("/tags", response_model=TagRead, status_code=201)
async def create_tag(
    body: TagCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    tag = await tag_service.create_tag(session, body.model_dump())
    # Backfill the new tag across all existing jobs
    background_tasks.add_task(_backfill_tag, tag.id)
    return tag


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(tag_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    deleted = await tag_service.delete_tag(session, tag_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tag not found")
