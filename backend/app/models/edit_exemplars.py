"""Convergence-record exemplars: per (doc, section), what did the LLM first
produce vs. where did the user actually land?

Used as in-context personalization signal for both the brainstorm and edit
flows. One row per (doc_id, section_path). On each user-facing edit, the row
is upserted: first time creates `original_llm_value` from the pre-edit value;
subsequent edits update `final_user_value` and bump `iteration_count`. The
original is never overwritten — the exemplar is *the gap* between what the
LLM tends to produce and what the user actually wants.

Dismissed brainstorm cards write nothing here — they're abandoned proposals,
not corrections.
"""

import uuid

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class EditExemplar(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "edit_exemplars"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # e.g. "summary", "tagline", "selected_research.2.description",
    # "experience.rand.bullets.0"
    section_path: Mapped[str] = mapped_column(Text, nullable=False)
    # Coarse type label so retrieval can bias toward same-shape edits.
    # e.g. "summary" | "tagline" | "research_description" | "bullet" | "skills"
    entity_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    # The pre-edit content the first time this passage was touched. Whatever
    # was there at that moment was almost certainly LLM-generated (by
    # resume_builder, an earlier brainstorm, or an earlier edit pass).
    # Stored as text — JSON-encoded for non-string sections.
    original_llm_value: Mapped[str] = mapped_column(Text, nullable=False)
    # Updated on every iteration; always the latest user-accepted version.
    final_user_value: Mapped[str] = mapped_column(Text, nullable=False)

    iteration_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    # JSONB array of user instructions, oldest-first. Each entry is
    # {"text": "...", "source": "action_card"|"edit_section"|"broad_rewrite",
    #  "at": "<iso timestamp>"}. The most-recent instruction's text drives
    # the embedding.
    instructions: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Where the FIRST edit came from (resume_gen if the original_llm_value
    # was just whatever resume_builder produced; otherwise the user-facing
    # source).
    source_first: Mapped[str] = mapped_column(Text, nullable=False)

    # 1536-d embedding of (most-recent instruction + entity_type), stored as
    # a JSON list for portability (matches the discovered_companies pattern).
    instruction_embedding: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("doc_id", "section_path", name="uq_edit_exemplars_doc_section"),
    )
