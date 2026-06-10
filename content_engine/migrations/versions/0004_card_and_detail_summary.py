"""add card_summary and detail_summary to events (stage 1.4)

Revision ID: 0004_card_and_detail_summary
Revises: 0003_source_health
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_card_and_detail_summary"
down_revision: Union[str, None] = "0003_source_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("card_summary", sa.String(length=180), nullable=True),
    )
    op.add_column(
        "events",
        sa.Column("detail_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "detail_summary")
    op.drop_column("events", "card_summary")
