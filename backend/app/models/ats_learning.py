"""Models for self-improving ATS URL resolution.

AtsDomainCache: exact domain-to-ATS mappings (always verified, always safe).
AtsLearnedPattern: generalizable regex rules discovered by LLM (carry risk, auto-disabled on failures).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class AtsDomainCache(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ats_domain_cache"

    domain: Mapped[str] = mapped_column(Text, unique=True, index=True, nullable=False)
    ats: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    source_method: Mapped[str] = mapped_column(Text, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AtsLearnedPattern(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ats_learned_patterns"

    ats: Mapped[str] = mapped_column(Text, nullable=False)
    pattern: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    slug_group: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
