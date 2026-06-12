"""add events.needs_split / suggested_merge_id for HDBSCAN offline review (stage 2.4)

Revision ID: 0007_event_review_fields
Revises: 0006_embedding_dim_512
Create Date: 2026-06-10

阶段 2.4：HDBSCAN 离线复核仅"打标"，不自动改 DB 归属。新增两个可空字段：
- needs_split           : 是否建议人工拆分该事件
- suggested_merge_id    : 建议合并到的目标事件 id（人工在 CMS 决策）
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_event_review_fields"
down_revision: Union[str, None] = "0006_embedding_dim_512"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("needs_split", sa.Boolean(), nullable=True))
    op.add_column(
        "events",
        sa.Column("suggested_merge_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "suggested_merge_id")
    op.drop_column("events", "needs_split")
