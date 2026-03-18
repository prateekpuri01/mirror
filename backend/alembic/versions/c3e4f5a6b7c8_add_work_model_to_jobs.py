"""add work_model column to jobs

Revision ID: c3e4f5a6b7c8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-17 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c3e4f5a6b7c8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("work_model", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "work_model")
