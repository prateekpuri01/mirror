"""Pipeline and liveness response schemas."""

from pydantic import BaseModel


class PipelineProcessResult(BaseModel):
    cleaned: int = 0
    scored: int = 0
    skipped: int = 0
    errors: int = 0
    expired_excluded: int = 0


class PipelineStatus(BaseModel):
    scraped: int = 0
    cleaned: int = 0
    scored: int = 0
    skipped: int = 0
    total: int = 0
    expired: int = 0


class RescoreResult(BaseModel):
    rescored: int = 0
    failed: int = 0
    current_version: str = ""
    job_ids: list[str] = []


class PrefilterResult(BaseModel):
    passed: int = 0
    skipped: int = 0
    already_filtered: int = 0
    total: int = 0


class LivenessStats(BaseModel):
    total_expired: int = 0
    expired_last_7d: int = 0
    expired_last_30d: int = 0
    total_active: int = 0


class RecheckExpiredResult(BaseModel):
    checked: int = 0
    revived: int = 0
    confirmed_dead: int = 0
    errors: int = 0


class GCResult(BaseModel):
    archived: int = 0


class ReprocessRequest(BaseModel):
    job_ids: list[str]
    operations: list[str]  # "score" | "extract" | "requirements"


class ReprocessResult(BaseModel):
    job_ids: list[str] = []
