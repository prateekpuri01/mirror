"""Runtime-overridable settings persisted across container rebuilds.

The ``Settings`` class in ``app/config.py`` is read once at process start
from environment variables. That's the right model for infra config
(database URL, log level) but the wrong model for things a user enters
through the UI — API keys, provider choice, optional model overrides.

This table is the persistence layer for the ``/setup`` wizard. The
FastAPI lifespan hook reads every row at startup and overrides the
matching field on the global ``settings`` object, so existing
``settings.openai_api_key`` references keep working. ``/setup`` writes
into this table, then triggers a reload + an LLM-client reset.

Env vars still work as a fallback / power-user override — DB rows take
precedence when both are present.
"""

from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
