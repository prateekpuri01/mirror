"""add edit_exemplars table

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-06-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "edit_exemplars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "doc_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_path", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("original_llm_value", sa.Text(), nullable=False),
        sa.Column("final_user_value", sa.Text(), nullable=False),
        sa.Column(
            "iteration_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("instructions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_first", sa.Text(), nullable=False),
        sa.Column(
            "instruction_embedding",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
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
        sa.UniqueConstraint("doc_id", "section_path", name="uq_edit_exemplars_doc_section"),
    )
    op.create_index("ix_edit_exemplars_job_id", "edit_exemplars", ["job_id"])
    op.create_index("ix_edit_exemplars_doc_id", "edit_exemplars", ["doc_id"])
    op.create_index("ix_edit_exemplars_entity_type", "edit_exemplars", ["entity_type"])


def downgrade() -> None:
    op.drop_index("ix_edit_exemplars_entity_type", table_name="edit_exemplars")
    op.drop_index("ix_edit_exemplars_doc_id", table_name="edit_exemplars")
    op.drop_index("ix_edit_exemplars_job_id", table_name="edit_exemplars")
    op.drop_table("edit_exemplars")
