"""add eightfold ATS support

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-04-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "l6m7n8o9p0q1"
down_revision: Union[str, None] = "k5l6m7n8o9p0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add eightfold enum value to job_source
    op.execute(sa.text("ALTER TYPE job_source ADD VALUE IF NOT EXISTS 'eightfold'"))

    # Add eightfold_slug column to companies
    op.add_column("companies", sa.Column("eightfold_slug", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "eightfold_slug")
