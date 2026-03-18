import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SearchProfileCreate(BaseModel):
    name: str
    keywords: list[str] = []
    locations: list[str] = []
    remote_ok: bool = True
    salary_min: int | None = None
    experience_level: str | None = None


class SearchProfileUpdate(BaseModel):
    name: str | None = None
    keywords: list[str] | None = None
    locations: list[str] | None = None
    remote_ok: bool | None = None
    salary_min: int | None = None
    experience_level: str | None = None
    is_active: bool | None = None


class SearchProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    keywords: list[str]
    locations: list[str]
    remote_ok: bool
    salary_min: int | None = None
    experience_level: str | None = None
    is_active: bool
    created_at: datetime
