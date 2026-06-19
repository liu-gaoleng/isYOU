"""add user_id to favorites/reading_history + push_settings table (stage 3.4)

Revision ID: 0012_user_collections
Revises: 0011_user_member_fields
Create Date: 2026-06-14

阶段 3.4：收藏 / 阅读历史 / 推送设置从 mock 迁生产。
- favorites / reading_history 新增 nullable user_id 列（生产 C 端按 JWT 真实用户 id；
  既有 token 列改 nullable，与 mock 联调态并存，互不干扰）；
- 新建 push_settings 表（每个登录用户一行）。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_user_collections"
down_revision: Union[str, None] = "0011_user_member_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def upgrade() -> None:
    # favorites：加 user_id（nullable），token 改 nullable，补用户维度唯一约束/索引
    op.add_column("favorites", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.alter_column("favorites", "token", existing_type=sa.String(length=64), nullable=True)
    op.create_unique_constraint(
        "uq_favorites_user_event", "favorites", ["user_id", "event_id"]
    )
    op.create_index("ix_favorites_user_id", "favorites", ["user_id"])

    # reading_history：同上
    op.add_column("reading_history", sa.Column("user_id", sa.BigInteger(), nullable=True))
    op.alter_column(
        "reading_history", "token", existing_type=sa.String(length=64), nullable=True
    )
    op.create_index(
        "ix_reading_history_user_viewed", "reading_history", ["user_id", "viewed_at"]
    )

    # push_settings：每个登录用户一行
    op.create_table(
        "push_settings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("daily_push", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("push_time", sa.String(length=8), nullable=False, server_default="08:00"),
        sa.Column(
            "breaking_push", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.UniqueConstraint("user_id", name="uq_push_settings_user_id"),
    )


def downgrade() -> None:
    op.drop_table("push_settings")

    op.drop_index("ix_reading_history_user_viewed", table_name="reading_history")
    op.alter_column(
        "reading_history", "token", existing_type=sa.String(length=64), nullable=False
    )
    op.drop_column("reading_history", "user_id")

    op.drop_index("ix_favorites_user_id", table_name="favorites")
    op.drop_constraint("uq_favorites_user_event", "favorites", type_="unique")
    op.alter_column("favorites", "token", existing_type=sa.String(length=64), nullable=False)
    op.drop_column("favorites", "user_id")
