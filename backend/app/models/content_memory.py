"""Per-entity content memory.

Stores the user's hand-tuned final text for resume content units (research
descriptions, experience bullet sets, skill buckets, summary, tagline). Past
final versions are fed into future generations as grounding examples — they
inform tone and phrasing without being copied verbatim.

Differs from ``writing_memory``: that table extracts abstract style rules
("use 'built' instead of 'utilized'"). This table stores actual content,
keyed to the underlying entity, so the agent can see how the user has
previously written *this specific* accomplishment / skill bucket / etc.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class ContentMemory(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "content_memory"

    # 'research_description' | 'experience_bullets_set' | 'skill_bucket' | 'summary' | 'tagline'
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)

    # accomplishment_id | <employer_key> | <bucket_name> | '__scalar__'
    entity_key: Mapped[str] = mapped_column(Text, nullable=False)

    # Pegged to the source resume so multiple edits within one resume collapse
    # onto the same row (final-state semantics). Across resumes, history
    # accumulates — those rows become the grounding corpus.
    source_doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Scalar text fields land here; bullet sets land in user_payload_json.
    user_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # {job_title, company_name, score_rationale_excerpt} — captured at save
    # time so the LLM can see what role this final version was tuned for.
    job_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # sha256 of the underlying accomplishment(s) at save time. On lookup,
    # if the live profile no longer matches, the example is demoted to a
    # soft stylistic reference.
    source_text_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_key", "source_doc_id",
            name="uq_content_memory_entity_doc",
        ),
        Index(
            "ix_content_memory_lookup",
            "entity_type", "entity_key", "is_active", "updated_at",
        ),
    )
