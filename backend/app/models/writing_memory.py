"""Model for persistent writing preference memory.

Tracks patterns learned from user edits to AI-generated resumes and
short-answer responses so future generations avoid repeating mistakes.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class WritingMemory(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "writing_memory"

    # Which prompt pipeline consumes this rule
    domain: Mapped[str] = mapped_column(Text, nullable=False, index=True)  # "resume" | "answer"

    # The natural-language rule injected into prompts
    rule_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Classification
    category: Mapped[str] = mapped_column(Text, nullable=False)  # word_choice | tone | structure | content | formatting
    scope: Mapped[str] = mapped_column(Text, nullable=False, server_default="universal")  # universal | job_specific

    # Before/after examples: [{"before": "...", "after": "..."}]
    examples_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Provenance
    source_type: Mapped[str] = mapped_column(Text, nullable=False)  # auto_inline_edit | auto_chat_edit | auto_answer_edit | explicit_user
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Confidence & lifecycle
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
