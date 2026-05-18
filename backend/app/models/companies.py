import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey
from app.models.jobs import JobSource


class Company(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    greenhouse_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    ashby_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    lever_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    eightfold_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    careers_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    include_patterns: Mapped[list | None] = mapped_column(
        JSONB(astext_type=Text()), nullable=True
    )
    exclude_patterns: Mapped[list | None] = mapped_column(
        JSONB(astext_type=Text()), nullable=True
    )
    monitoring_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    last_scraped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    aliases: Mapped[list | None] = mapped_column(
        JSONB(astext_type=Text()), nullable=True, server_default=text("'[]'::jsonb")
    )


class ScrapeRun(UUIDPrimaryKey, Base):
    __tablename__ = "scrape_runs"

    source: Mapped[JobSource] = mapped_column(
        Enum(JobSource, name="job_source", create_constraint=False),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text, server_default=text("'pending'"), nullable=False
    )
    jobs_found: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    jobs_new: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    jobs_updated: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    jobs_expired: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
