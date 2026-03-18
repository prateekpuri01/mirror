"""add enriched_at to companies

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-16 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: mark companies with substantive notes as already enriched
    op.execute(
        "UPDATE companies SET enriched_at = updated_at WHERE length(notes) > 100"
    )


def downgrade() -> None:
    op.drop_column("companies", "enriched_at")
