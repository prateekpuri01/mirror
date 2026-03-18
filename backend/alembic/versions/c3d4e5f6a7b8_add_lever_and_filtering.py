"""add lever slug and keyword filtering to companies

Revision ID: c3d4e5f6a7b8
Revises: b7e2f1a3c9d8
Create Date: 2026-03-16 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b7e2f1a3c9d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add lever enum value
    op.execute(sa.text("ALTER TYPE job_source ADD VALUE IF NOT EXISTS 'lever'"))

    # Add lever_slug and filtering columns to companies
    op.add_column("companies", sa.Column("lever_slug", sa.Text(), nullable=True))
    op.add_column(
        "companies",
        sa.Column(
            "include_patterns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "companies",
        sa.Column(
            "exclude_patterns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "exclude_patterns")
    op.drop_column("companies", "include_patterns")
    op.drop_column("companies", "lever_slug")
