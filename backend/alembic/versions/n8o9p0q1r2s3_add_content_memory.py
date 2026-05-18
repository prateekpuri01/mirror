"""add content_memory table

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-04-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_key", sa.Text(), nullable=False),
        sa.Column(
            "source_doc_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("user_text", sa.Text(), nullable=True),
        sa.Column("user_payload_json", postgresql.JSONB(), nullable=True),
        sa.Column("job_context", postgresql.JSONB(), nullable=True),
        sa.Column("source_text_hash", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "entity_type", "entity_key", "source_doc_id",
            name="uq_content_memory_entity_doc",
        ),
    )
    op.create_index(
        "ix_content_memory_lookup",
        "content_memory",
        ["entity_type", "entity_key", "is_active", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_memory_lookup", table_name="content_memory")
    op.drop_table("content_memory")
