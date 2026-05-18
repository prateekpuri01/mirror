"""API endpoints for managing writing memory rules."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.writing_memory import (
    WritingMemoryCreate,
    WritingMemoryRead,
    WritingMemoryUpdate,
)
from app.services import writing_memory_service as svc

router = APIRouter(prefix="/api/writing-memory", tags=["writing-memory"])


@router.get("", response_model=list[WritingMemoryRead])
async def list_rules(
    domain: str | None = Query(None),
    scope: str | None = Query(None),
    is_active: bool | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    return await svc.get_all_rules(session, domain=domain, scope=scope, is_active=is_active)


@router.post("", response_model=WritingMemoryRead, status_code=201)
async def create_rule(
    body: WritingMemoryCreate,
    session: AsyncSession = Depends(get_session),
):
    rule = await svc.add_rule(
        session,
        domain=body.domain,
        rule_text=body.rule_text,
        category=body.category,
        scope=body.scope,
        examples_json=body.examples_json,
        source_type="explicit_user",
    )
    await session.commit()
    return rule


@router.get("/{rule_id}", response_model=WritingMemoryRead)
async def get_rule(
    rule_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    rule = await svc.get_rule(session, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.patch("/{rule_id}", response_model=WritingMemoryRead)
async def update_rule(
    rule_id: uuid.UUID,
    body: WritingMemoryUpdate,
    session: AsyncSession = Depends(get_session),
):
    rule = await svc.update_rule(
        session,
        rule_id,
        **body.model_dump(exclude_none=True),
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    await session.commit()
    return rule


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
):
    rule = await svc.deactivate_rule(session, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    await session.commit()
    return {"ok": True}


@router.post("/consolidate")
async def consolidate(
    domain: str = Query("resume"),
    session: AsyncSession = Depends(get_session),
):
    """LLM-distill the user-visible rule set into a tighter, deduped set.

    Returns a structured report so the UI can show what was merged.
    """
    from app.ai.writing_memory import consolidate_rules
    report = await consolidate_rules(session, domain)
    await session.commit()
    return report


@router.post("/decay")
async def run_decay(
    session: AsyncSession = Depends(get_session),
):
    """Decay stale rules and deactivate expired ones."""
    decayed, deactivated = await svc.decay_stale_rules(session)
    await session.commit()
    return {"decayed": decayed, "deactivated": deactivated}
