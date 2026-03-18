"""add pipeline stage tracking, liveness, and prefilter columns

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-03-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "j4k5l6m7n8o9"
down_revision: Union[str, None] = "i3j4k5l6m7n8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Workstream 1: Pipeline stage tracking ---
    op.add_column("jobs", sa.Column("pipeline_stage", sa.Text(), nullable=False, server_default="scraped"))
    op.add_column("jobs", sa.Column("cleaned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("score_prompt_version", sa.Text(), nullable=True))

    # --- Workstream 2: Job liveness & expiration ---
    op.add_column("jobs", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("scrape_runs", sa.Column("jobs_expired", sa.Integer(), nullable=False, server_default="0"))

    # --- Workstream 3: Prefilter ---
    op.add_column("jobs", sa.Column("prefilter_pass", sa.Boolean(), nullable=True))

    # --- Backfill pipeline_stage from existing data ---
    # Jobs with scores → 'scored'
    op.execute("""
        UPDATE jobs
        SET pipeline_stage = 'scored',
            scored_at = updated_at,
            score_prompt_version = score_rationale->>'prompt_version'
        WHERE role_fit_score IS NOT NULL
    """)
    # Jobs with cleaning done but not scored → 'cleaned'
    op.execute("""
        UPDATE jobs
        SET pipeline_stage = 'cleaned',
            cleaned_at = updated_at
        WHERE pipeline_stage = 'scraped'
          AND (clean_title IS NOT NULL OR clean_company IS NOT NULL)
    """)

    # Index for pipeline queries
    op.create_index("ix_jobs_pipeline_stage", "jobs", ["pipeline_stage"])
    op.create_index("ix_jobs_expired_at", "jobs", ["expired_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_expired_at", table_name="jobs")
    op.drop_index("ix_jobs_pipeline_stage", table_name="jobs")
    op.drop_column("jobs", "prefilter_pass")
    op.drop_column("scrape_runs", "jobs_expired")
    op.drop_column("jobs", "expired_at")
    op.drop_column("jobs", "last_seen_at")
    op.drop_column("jobs", "score_prompt_version")
    op.drop_column("jobs", "scored_at")
    op.drop_column("jobs", "cleaned_at")
    op.drop_column("jobs", "pipeline_stage")
