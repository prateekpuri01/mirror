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
    doc_id: uuid.UUID | None = None  # which resume version to edit (None = latest)
    # When set, skips the LLM intent classifier and routes the agent directly.
    # Used for deterministic UI buttons (e.g. the "Proofread" button) that
    # should always reach the same node regardless of how the user phrases it.
    intent_override: str | None = None
