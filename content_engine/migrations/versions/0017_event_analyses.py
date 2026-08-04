"""add event_analyses table（热点透视）

Revision ID: 0017_event_analyses
Revises: 0016_push_tz
Create Date: 2026-08-04

热点透视：单事件按需 LLM 深度分析（来龙去脉/现状剖析/趋势推演，会员专属）。
event_id 唯一约束即幂等锚点（一次生成、全会员共享）；status 用 String(16)
不用 PgEnum，与 ops 域取舍一致（SQLite 单测可建表、免枚举迁移负担）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_event_analyses"
down_revision: str | None = "0016_push_tz"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "event_analyses",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column(
            "event_id",
            sa.BigInteger(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("sections", postgresql.JSONB(), nullable=True),
        sa.Column(
            "related_event_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("llm_meta", postgresql.JSONB(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_event_analyses_event_id"),
    )


def downgrade() -> None:
    op.drop_table("event_analyses")
