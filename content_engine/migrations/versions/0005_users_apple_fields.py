"""create users table with apple sign-in fields (stage 1.5)

Revision ID: 0005_users_apple_fields
Revises: 0004_card_and_detail_summary
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_users_apple_fields"
down_revision: Union[str, None] = "0004_card_and_detail_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("apple_user_id", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=256), nullable=True),
        sa.Column(
            "created_via",
            sa.String(length=16),
            nullable=False,
            server_default="apple",
        ),
        sa.Column("display_name", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("apple_user_id", name="uq_users_apple_user_id"),
    )
    op.create_index("ix_users_email", "users", ["email"])


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
