import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    role: str
    content: str
    section_context: str | None = None
    created_at: datetime


class ChatMessageCreate(BaseModel):
    content: str
    section_context: str | None = None
