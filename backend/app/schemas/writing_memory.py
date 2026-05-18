"""Pydantic schemas for the writing memory system."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class WritingMemoryCreate(BaseModel):
    domain: str = Field(..., pattern="^(resume|answer)$")
    rule_text: str = Field(..., min_length=3, max_length=1000)
    category: str = Field(..., pattern="^(word_choice|tone|structure|content|formatting)$")
    scope: str = Field(default="universal", pattern="^(universal|job_specific)$")
    examples_json: list[dict[str, str]] | None = None


class WritingMemoryUpdate(BaseModel):
    rule_text: str | None = Field(default=None, min_length=3, max_length=1000)
    category: str | None = Field(default=None, pattern="^(word_choice|tone|structure|content|formatting)$")
    scope: str | None = Field(default=None, pattern="^(universal|job_specific)$")
    is_active: bool | None = None


class WritingMemoryRead(BaseModel):
    id: UUID
    domain: str
    rule_text: str
    category: str
    scope: str
    examples_json: list[dict[str, str]] | None
    source_type: str
    source_job_id: UUID | None
    confidence: float
    occurrence_count: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
