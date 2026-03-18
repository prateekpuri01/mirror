"""add cleaning columns to jobs and aliases to companies

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-03-17 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Jobs: cleaning columns ---
    op.add_column("jobs", sa.Column("clean_title", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("clean_company", sa.Text(), nullable=True))

    # Generated columns: COALESCE(clean_*, raw_*)
    # PostgreSQL 12+ GENERATED ALWAYS AS ... STORED
    op.execute(
        "ALTER TABLE jobs ADD COLUMN display_title text "
        "GENERATED ALWAYS AS (COALESCE(clean_title, title)) STORED"
    )
    op.execute(
        "ALTER TABLE jobs ADD COLUMN display_company text "
        "GENERATED ALWAYS AS (COALESCE(clean_company, company)) STORED"
    )

    # --- Companies: aliases ---
    op.add_column(
        "companies",
        sa.Column("aliases", JSONB(astext_type=sa.Text()), nullable=True, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("jobs", "display_company")
    op.drop_column("jobs", "display_title")
    op.drop_column("jobs", "clean_company")
    op.drop_column("jobs", "clean_title")
    op.drop_column("companies", "aliases")
