import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.jobs import JobSource, JobStatus
from app.schemas.locations import LocationRead


class JobCreate(BaseModel):
    title: str
    company: str
    location: str | None = None
    remote: bool = False
    salary_min: int | None = None
    salary_max: int | None = None
    description: str
    url: str
    source: JobSource = JobSource.manual


class JobUpdate(BaseModel):
    status: JobStatus | None = None
    thumbs: int | None = None
    user_notes: str | None = None
    relevance_score: float | None = None
    role_fit_score: int | None = None
    interest_fit_score: int | None = None
    score_rationale: dict | None = None
    location: str | None = None
    remote: bool | None = None
    salary_min: int | None = None
    salary_max: int | None = None


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    color: str | None = None


class SearchProfileBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str


class AppReqBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    needs_resume: bool
    needs_cover_letter: bool
    needs_short_answers: bool


class DocumentBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    doc_type: str
    name: str
    version: int
    content_docx_path: str | None = None


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    company: str
    company_id: uuid.UUID | None = None
    location: str | None = None
    remote: bool
    work_model: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    description: str
    description_html: str | None = None
    url: str
    application_url: str | None = None
    source: JobSource
    posted_at: datetime | None = None
    scraped_at: datetime
    status: JobStatus
    relevance_score: float | None = None
    role_fit_score: int | None = None
    interest_fit_score: int | None = None
    score_rationale: dict | None = None
    thumbs: int | None = None
    user_notes: str | None = None
    extra_metadata: dict | None = None
    clean_title: str | None = None
    clean_company: str | None = None
    display_title: str
    display_company: str
    pipeline_stage: str = "scraped"
    cleaned_at: datetime | None = None
    scored_at: datetime | None = None
    score_prompt_version: str | None = None
    last_seen_at: datetime | None = None
    expired_at: datetime | None = None
    prefilter_pass: bool | None = None
    created_at: datetime
    updated_at: datetime

    tags: list[TagRead] = []
    search_profiles: list[SearchProfileBrief] = []
    application_requirements: AppReqBrief | None = None
    documents: list[DocumentBrief] = []
    normalized_locations: list[LocationRead] = []


class JobList(BaseModel):
    items: list[JobRead]
    total: int
    page: int
    per_page: int
