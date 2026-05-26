import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str] = mapped_column(Text, nullable=False)
    is_remote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    jobs: Mapped[list["Job"]] = relationship(  # noqa: F821
        secondary="job_locations", back_populates="normalized_locations", lazy="selectin"
    )


class JobLocation(Base):
    __tablename__ = "job_locations"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True
    )
