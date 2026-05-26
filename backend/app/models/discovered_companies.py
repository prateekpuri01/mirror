"""Discovery cache — companies surfaced by hot search v2, persisted so
subsequent searches can recall them without re-paying the discovery cost.

This is intentionally separate from ``companies`` (which tracks companies
the user has actively imported jobs from). A row here means we *found*
the company once — it may or may not have had matching jobs at the time,
but the resolved metadata (ATS slug, careers URL) is worth keeping.

Recall pattern:
  Phase A2 of hot search v2 unions LLM-web discoveries + aggregator
  entries + the K most cosine-similar rows from this table. So when
  query Q1 finds Acme on day 1, query Q2 a week later sees Acme as a
  candidate without paying the LLM-web cost to re-surface it.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DiscoveredCompany(TimestampMixin, Base):
    __tablename__ = "discovered_companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Lowercased, suffix-stripped form used for upsert dedup. Unique.
    # E.g. "Anthropic, PBC" → "anthropic".
    normalized_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    # Last-observed display name — we keep the most recent spelling.
    name: Mapped[str] = mapped_column(Text, nullable=False)

    # Resolution outputs. Populated lazily as we learn them.
    ats: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # "greenhouse" | "lever" | "ashby" | "direct"
    slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    careers_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Short blurb (one paragraph max) used both for UI and as the text
    # we embed for cosine recall. Populated opportunistically from LLM
    # output during discovery; may be empty.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 1536-d float array (text-embedding-3-small). Stored as JSONB for
    # portability — we're not at pgvector scale yet. JSONB lets us
    # filter via SQL if needed and dodge a binary-format migration.
    description_embedding: Mapped[list | None] = mapped_column(
        JSONB(astext_type=Text()), nullable=True
    )

    # Where we found it. Useful for analytics + tuning the mix.
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'")
    )  # "llm_web" | "aggregator" | "manual"

    # The query text that surfaced this company on its most recent hit.
    # Helps debug "why was this company recalled for THIS search?"
    last_query: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Outcome stamps. "discovered" is the default initial state; updated
    # to "hit" when the company produced matching jobs, "no_jobs" when
    # scrape returned nothing, "no_match" when the rerank rejected it.
    # We use this to down-weight companies that have failed to match
    # multiple times, without deleting them outright.
    last_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'discovered'")
    )

    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    times_matched: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    times_no_jobs: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
