"""add discovered_companies table

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "p0q1r2s3t4u5"
down_revision = "o9p0q1r2s3t4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovered_companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("normalized_name", sa.Text(), unique=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ats", sa.Text(), nullable=True),
        sa.Column("slug", sa.Text(), nullable=True),
        sa.Column("careers_url", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "description_embedding",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("source", sa.Text(), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("last_query", sa.Text(), nullable=True),
        sa.Column("last_status", sa.Text(), server_default=sa.text("'discovered'"), nullable=False),
        sa.Column("times_seen", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("times_matched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("times_no_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_discovered_companies_normalized_name",
        "discovered_companies",
        ["normalized_name"],
    )
    # Useful filter for the resolver-cache lookup (skip rows with no
    # resolved ATS or careers URL when we want only "resolvable" rows).
    op.create_index(
        "ix_discovered_companies_resolution",
        "discovered_companies",
        ["ats", "careers_url"],
    )


def downgrade() -> None:
    op.drop_index("ix_discovered_companies_resolution", table_name="discovered_companies")
    op.drop_index("ix_discovered_companies_normalized_name", table_name="discovered_companies")
    op.drop_table("discovered_companies")
