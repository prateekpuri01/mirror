import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CompanyCreate(BaseModel):
    name: str
    website: str | None = None
    greenhouse_slug: str | None = None
    ashby_slug: str | None = None
    lever_slug: str | None = None
    careers_url: str | None = None
    notes: str | None = None
    monitoring_active: bool = True
    include_patterns: list[str] | None = None
    exclude_patterns: list[str] | None = None
    aliases: list[str] | None = None


class CompanyUpdate(BaseModel):
    name: str | None = None
    website: str | None = None
    greenhouse_slug: str | None = None
    ashby_slug: str | None = None
    lever_slug: str | None = None
    careers_url: str | None = None
    notes: str | None = None
    monitoring_active: bool | None = None
    include_patterns: list[str] | None = None
    exclude_patterns: list[str] | None = None
    aliases: list[str] | None = None


class CompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    website: str | None = None
    greenhouse_slug: str | None = None
    ashby_slug: str | None = None
    lever_slug: str | None = None
    careers_url: str | None = None
    notes: str | None = None
    monitoring_active: bool
    include_patterns: list[str] | None = None
    exclude_patterns: list[str] | None = None
    aliases: list[str] | None = None
    last_scraped_at: datetime | None = None
    enriched_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CompanyListRead(CompanyRead):
    """CompanyRead with computed job_count and sources."""

    job_count: int = 0
    sources: list[str] = []


class CompanyList(BaseModel):
    items: list[CompanyListRead]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Discovery / Import schemas
# ---------------------------------------------------------------------------


class DiscoverRequest(BaseModel):
    job_url: str


class JobPreview(BaseModel):
    title: str
    location: str | None = None
    department: str | list[str] | None = None
    url: str
    posted_at: str | None = None
    relevance: int = 0
    description_html: str | None = None
    remote: bool = False


class DiscoverResponse(BaseModel):
    ats: str | None = None
    slug: str | None = None
    company_name: str | None = None
    total_jobs: int = 0
    jobs: list[JobPreview] = []
    resolution_method: str | None = None  # "regex", "fingerprint", "llm", "unresolved"
    error: str | None = None


class RefreshResponse(BaseModel):
    ats: str | None = None
    slug: str | None = None
    company_name: str | None = None
    total_jobs: int = 0
    jobs: list[JobPreview] = []
    expired_count: int = 0
    error: str | None = None


class ImportRequest(BaseModel):
    name: str
    website: str | None = None
    ats: str
    slug: str
    selected_urls: list[str] | None = None
    monitoring_active: bool = True


class ImportResponse(BaseModel):
    company_id: str
    company_name: str | None = None
    jobs_imported: int = 0
    job_ids: list[str] = []


# ---------------------------------------------------------------------------
# Hot Company Search schemas
# ---------------------------------------------------------------------------


class HotSearchRequest(BaseModel):
    sources: list[str]  # ["tavily", "greenhouse", "lever", "ashby"]
    guidance: str = ""
    max_hits: int = 20
