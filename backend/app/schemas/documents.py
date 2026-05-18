import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.documents import DocType


from typing import Any


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID | None = None
    doc_type: DocType
    name: str
    content_markdown: str | None = None
    content_json: dict | None = None
    content_docx_path: str | None = None
    is_base_template: bool
    version: int
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    content_markdown: str


class DocumentRevise(BaseModel):
    instruction: str


class SectionUpdate(BaseModel):
    path: str
    value: Any


class ResearchEntryRequest(BaseModel):
    accomplishment_id: str
    index: int  # which selected_research slot to replace (0, 1, or 2)


class BulletGenerationRequest(BaseModel):
    """Add-bullet-from-accomplishment payload.

    ``insert_at`` is optional — when omitted the new bullet is appended to
    the end of the employer's bullet array.
    """
    employer_key: str
    accomplishment_id: str
    insert_at: int | None = None


class SectionHistoryItem(BaseModel):
    id: uuid.UUID
    job_title: str | None = None
    company_name: str | None = None
    updated_at: datetime
    preview: str
    user_text: str | None = None
    user_payload_json: Any | None = None


class SectionHistoryResponse(BaseModel):
    entity_type: str
    entity_key: str
    items: list[SectionHistoryItem]
