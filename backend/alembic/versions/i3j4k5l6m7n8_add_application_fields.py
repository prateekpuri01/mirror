"""add application_fields to application_requirements

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-03-17 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "i3j4k5l6m7n8"
down_revision: Union[str, None] = "h2i3j4k5l6m7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "application_requirements",
        sa.Column("application_fields", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_requirements", "application_fields")
