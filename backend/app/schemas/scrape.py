import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.jobs import JobSource


class ScrapeRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: JobSource
    company_id: uuid.UUID | None = None
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    jobs_found: int
    jobs_new: int
    jobs_updated: int
    jobs_expired: int = 0
    error_message: str | None = None


class ScrapeResult(BaseModel):
    runs: list[ScrapeRunRead]
    total_found: int
    total_new: int
    total_updated: int


class ScrapeRunList(BaseModel):
    items: list[ScrapeRunRead]
    total: int
    page: int
    per_page: int
