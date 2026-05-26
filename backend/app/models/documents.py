import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class DocType(enum.StrEnum):
    resume = "resume"
    cover_letter = "cover_letter"
    short_answer = "short_answer"
    other = "other"


class ApplicationRequirements(UUIDPrimaryKey, Base):
    __tablename__ = "application_requirements"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    needs_resume: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    needs_cover_letter: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_short_answers: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    short_answer_prompts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    needs_video_interview: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_other: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    other_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extraction columns
    cover_letter_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_answer_questions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    other_requirements: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    extraction_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    extraction_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_extraction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    application_fields: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    job: Mapped["Job"] = relationship(back_populates="application_requirements")


class Document(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "documents"

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    doc_type: Mapped[DocType] = mapped_column(
        Enum(DocType, name="doc_type", create_constraint=True), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    content_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    content_docx_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_base_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    job: Mapped["Job | None"] = relationship(back_populates="documents")
