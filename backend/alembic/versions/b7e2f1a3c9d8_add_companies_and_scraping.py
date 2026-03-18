"""add companies and scraping infrastructure

Revision ID: b7e2f1a3c9d8
Revises: fbb3a138fc16
Create Date: 2026-03-15 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7e2f1a3c9d8"
down_revision: Union[str, None] = "fbb3a138fc16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Expand job_source enum with new values
    op.execute(sa.text("ALTER TYPE job_source ADD VALUE IF NOT EXISTS 'ashby'"))
    op.execute(sa.text("ALTER TYPE job_source ADD VALUE IF NOT EXISTS 'company_website'"))

    # --- companies table ---
    op.create_table(
        "companies",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("greenhouse_slug", sa.Text(), nullable=True),
        sa.Column("ashby_slug", sa.Text(), nullable=True),
        sa.Column("careers_url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "monitoring_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # --- scrape_runs table ---
    op.create_table(
        "scrape_runs",
        sa.Column(
            "source",
            postgresql.ENUM(
                "greenhouse",
                "linkedin",
                "ai_discovered",
                "manual",
                "ashby",
                "company_website",
                name="job_source",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "jobs_found", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "jobs_new", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "jobs_updated", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- new columns on jobs ---
    op.add_column("jobs", sa.Column("company_id", sa.UUID(), nullable=True))
    op.add_column("jobs", sa.Column("application_url", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("description_html", sa.Text(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_foreign_key(
        "fk_jobs_company_id",
        "jobs",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_company_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "metadata")
    op.drop_column("jobs", "description_html")
    op.drop_column("jobs", "application_url")
    op.drop_column("jobs", "company_id")
    op.drop_table("scrape_runs")
    op.drop_table("companies")
    # Note: cannot remove values from PostgreSQL enum types
