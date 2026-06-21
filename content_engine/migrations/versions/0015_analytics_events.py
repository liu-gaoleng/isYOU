"""add analytics_events table (stage 4.3)

Revision ID: 0015_analytics_events
Revises: 0014_device_tokens
Create Date: 2026-06-21

阶段 4.3：自建埋点事件落库表。客户端批量上报，单事件单行；
仅按 (name, created_at) 与 (user_id, created_at) 建索引，覆盖按事件/按用户拉时间窗的漏斗查询。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_analytics_events"
down_revision: Union[str, None] = "0014_device_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("os_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("platform", sa.String(length=16), nullable=False, server_default="ios"),
        sa.Column("ts_client", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("props", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_analytics_events_name_created_at",
        "analytics_events",
        ["name", "created_at"],
    )
    op.create_index(
        "ix_analytics_events_user_id_created_at",
        "analytics_events",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_analytics_events_user_id_created_at", table_name="analytics_events")
    op.drop_index("ix_analytics_events_name_created_at", table_name="analytics_events")
    op.drop_table("analytics_events")
