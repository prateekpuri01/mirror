import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class JobSource(enum.StrEnum):
    greenhouse = "greenhouse"
    linkedin = "linkedin"
    ai_discovered = "ai_discovered"
    manual = "manual"
    ashby = "ashby"
    lever = "lever"
    hn_who_is_hiring = "hn_who_is_hiring"
    company_website = "company_website"
    eightfold = "eightfold"


class JobStatus(enum.StrEnum):
    new = "new"
    interested = "interested"
    applied = "applied"
    interviewing = "interviewing"
    rejected = "rejected"
    offer = "offer"
    archived = "archived"


class Job(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "jobs"

    title: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str] = mapped_column(Text, nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    work_model: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # "remote", "hybrid", "onsite"
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    description_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    application_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[JobSource] = mapped_column(
        Enum(JobSource, name="job_source", create_constraint=True), nullable=False
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", create_constraint=True),
        default=JobStatus.new,
        nullable=False,
    )
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    role_fit_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    interest_fit_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    score_rationale: Mapped[dict | None] = mapped_column(JSONB(astext_type=Text()), nullable=True)
    thumbs: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    user_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB(astext_type=Text()), nullable=True, server_default=text("'{}'::jsonb")
    )

    # Pipeline tracking
    pipeline_stage: Mapped[str] = mapped_column(
        Text, server_default=text("'scraped'"), nullable=False
    )  # scraped → cleaned → scored / skipped
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    score_prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Liveness tracking
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Pre-filter
    prefilter_pass: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Cleaning columns
    clean_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    clean_company: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_title: Mapped[str] = mapped_column(
        Text, Computed("COALESCE(clean_title, title)"), nullable=False
    )
    display_company: Mapped[str] = mapped_column(
        Text, Computed("COALESCE(clean_company, company)"), nullable=False
    )

    # Relationships
    tags: Mapped[list["Tag"]] = relationship(
        secondary="job_tags", back_populates="jobs", lazy="selectin"
    )
    search_profiles: Mapped[list["SearchProfile"]] = relationship(
        secondary="job_search_profile", back_populates="jobs", lazy="selectin"
    )
    application_requirements: Mapped["ApplicationRequirements | None"] = relationship(
        back_populates="job", uselist=False, lazy="selectin"
    )
    documents: Mapped[list["Document"]] = relationship(
        back_populates="job", lazy="selectin", order_by="Document.created_at.desc()"
    )
    normalized_locations: Mapped[list["Location"]] = relationship(  # noqa: F821
        secondary="job_locations", back_populates="jobs", lazy="selectin"
    )


class SearchProfile(UUIDPrimaryKey, Base):
    __tablename__ = "search_profiles"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    locations: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    remote_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    experience_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    jobs: Mapped[list["Job"]] = relationship(
        secondary="job_search_profile", back_populates="search_profiles", lazy="selectin"
    )


class JobSearchProfile(Base):
    __tablename__ = "job_search_profile"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    search_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Tag(UUIDPrimaryKey, Base):
    __tablename__ = "tags"

    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)

    # Relationships
    jobs: Mapped[list["Job"]] = relationship(
        secondary="job_tags", back_populates="tags", lazy="selectin"
    )


class JobTag(Base):
    __tablename__ = "job_tags"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
