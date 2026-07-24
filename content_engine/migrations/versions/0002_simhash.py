"""add raw_articles.simhash for near-duplicate detection (stage 1.1)

Revision ID: 0002_simhash
Revises: 0001_initial
Create Date: 2026-06-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_simhash"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "raw_articles",
        sa.Column("simhash", sa.String(length=16), nullable=True),
    )
    op.create_index("ix_raw_articles_simhash", "raw_articles", ["simhash"])


def downgrade() -> None:
    op.drop_index("ix_raw_articles_simhash", table_name="raw_articles")
    op.drop_column("raw_articles", "simhash")
