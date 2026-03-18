"""add ats_domain_cache and ats_learned_patterns tables

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-03-18 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "k5l6m7n8o9p0"
down_revision: Union[str, None] = "j4k5l6m7n8o9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ats_domain_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("ats", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("source_method", sa.Text(), nullable=False),
        sa.Column("hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("domain"),
    )
    op.create_index("ix_ats_domain_cache_domain", "ats_domain_cache", ["domain"])

    op.create_table(
        "ats_learned_patterns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ats", sa.Text(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("slug_group", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("verified_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("pattern"),
    )


def downgrade() -> None:
    op.drop_table("ats_learned_patterns")
    op.drop_index("ix_ats_domain_cache_domain", table_name="ats_domain_cache")
    op.drop_table("ats_domain_cache")
