"""add push_settings.tz + widen push_records.biz_id (smoke fixes)

Revision ID: 0016_push_tz
Revises: 0015_analytics_events
Create Date: 2026-07-24

真机冒烟前修复（推送时区链路）：
1. ``push_settings.tz``：用户 IANA 时区（客户端上报），dispatcher 按各时区本地
   HH:MM 匹配 push_time；NULL 由代码层按 DEFAULT_PUSH_TZ（Asia/Shanghai）兜底，
   存量行无需回填。
2. ``push_records.biz_id`` 32 → 64：biz_id 改为按 (时区, 本地日期, HHMM) 维度
   幂等（如 ``daily-Asia-Shanghai-20260724-0800``），32 字符放不下。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_push_tz"
down_revision: str | None = "0015_analytics_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("push_settings", sa.Column("tz", sa.String(length=64), nullable=True))
    op.alter_column(
        "push_records",
        "biz_id",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "push_records",
        "biz_id",
        existing_type=sa.String(length=64),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.drop_column("push_settings", "tz")
