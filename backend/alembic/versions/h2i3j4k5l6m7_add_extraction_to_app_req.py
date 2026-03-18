"""add extraction columns to application_requirements

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-03-17 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "h2i3j4k5l6m7"
down_revision: Union[str, None] = "g1h2i3j4k5l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "application_requirements",
        sa.Column("cover_letter_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "application_requirements",
        sa.Column("short_answer_questions", JSONB, nullable=True),
    )
    op.add_column(
        "application_requirements",
        sa.Column("other_requirements", JSONB, nullable=True),
    )
    op.add_column(
        "application_requirements",
        sa.Column("extraction_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "application_requirements",
        sa.Column("extraction_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "application_requirements",
        sa.Column(
            "extracted_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "application_requirements",
        sa.Column("extraction_source_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "application_requirements",
        sa.Column("raw_extraction", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_requirements", "raw_extraction")
    op.drop_column("application_requirements", "extraction_source_url")
    op.drop_column("application_requirements", "extracted_at")
    op.drop_column("application_requirements", "extraction_error")
    op.drop_column("application_requirements", "extraction_status")
    op.drop_column("application_requirements", "other_requirements")
    op.drop_column("application_requirements", "short_answer_questions")
    op.drop_column("application_requirements", "cover_letter_status")
