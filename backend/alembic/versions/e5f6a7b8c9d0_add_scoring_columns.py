"""add scoring columns to jobs

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-16 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("role_fit_score", sa.SmallInteger(), nullable=True))
    op.add_column("jobs", sa.Column("interest_fit_score", sa.SmallInteger(), nullable=True))
    op.add_column("jobs", sa.Column("score_rationale", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "score_rationale")
    op.drop_column("jobs", "interest_fit_score")
    op.drop_column("jobs", "role_fit_score")
