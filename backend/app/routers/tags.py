import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.tags import TagCreate, TagRead
from app.services import tag_service

router = APIRouter(prefix="/api", tags=["tags"])


@router.get("/tags", response_model=list[TagRead])
async def list_tags(session: AsyncSession = Depends(get_session)):
    return await tag_service.list_tags(session)


@router.post("/tags", response_model=TagRead, status_code=201)
async def create_tag(body: TagCreate, session: AsyncSession = Depends(get_session)):
    tag = await tag_service.create_tag(session, body.model_dump())
    return tag


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(tag_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    deleted = await tag_service.delete_tag(session, tag_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tag not found")
