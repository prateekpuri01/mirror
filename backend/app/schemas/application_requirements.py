import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ShortAnswerQuestion(BaseModel):
    question: str
    max_length: int | None = None
    required: bool = True


class OtherRequirement(BaseModel):
    type: str
    description: str


class ApplicationField(BaseModel):
    label: str
    response_type: str  # "short_answer", "personal_info", or "fixed_response" (legacy: "free_text" treated as "short_answer")
    field_type: str | None = None  # "textarea", "select", "checkbox", "radio", etc.
    required: bool = True
    options: list[str] | None = None  # for fixed_response fields
    max_length: int | None = None  # for short_answer fields
    description: str | None = None
    draft_response: str | None = None  # AI-generated draft answer
    draft_history: list[dict] | None = None  # Editing history for iterative re-drafting


class AppReqCreate(BaseModel):
    needs_resume: bool = True
    needs_cover_letter: bool = False
    needs_short_answers: bool = False
    short_answer_prompts: dict | None = None
    needs_video_interview: bool = False
    needs_other: bool = False
    other_description: str | None = None


class AppReqRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    needs_resume: bool
    needs_cover_letter: bool
    needs_short_answers: bool
    short_answer_prompts: dict | None = None
    needs_video_interview: bool
    needs_other: bool
    other_description: str | None = None
    cover_letter_status: str | None = None
    short_answer_questions: list[ShortAnswerQuestion] | None = None
    other_requirements: list[OtherRequirement] | None = None
    extraction_status: str | None = None
    extraction_error: str | None = None
    extracted_at: datetime | None = None
    extraction_source_url: str | None = None
    raw_extraction: dict | None = None
    application_fields: list[ApplicationField] | None = None


class ExtractionStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    extraction_status: str | None = None
    extraction_error: str | None = None
    extracted_at: datetime | None = None
    extraction_source_url: str | None = None
