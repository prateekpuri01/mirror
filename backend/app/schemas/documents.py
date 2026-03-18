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
