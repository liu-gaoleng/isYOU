"""migrate embedding columns from 1024 dim (bge-large) to 512 dim (bge-small-zh-v1.5)

Revision ID: 0006_embedding_dim_512
Revises: 0005_users_apple_fields
Create Date: 2026-06-10

阶段 1.3：实际接入的是 bge-small-zh-v1.5（512 维），原占位 1024 维列从未填值，
因此这里直接 drop + 重建，避免维度不匹配导致 pgvector 报错。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0006_embedding_dim_512"
down_revision: Union[str, None] = "0005_users_apple_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # raw_articles.embedding：1024 → 512
    op.drop_column("raw_articles", "embedding")
    op.add_column(
        "raw_articles",
        sa.Column("embedding", Vector(512), nullable=True),
    )
    # events.centroid：1024 → 512
    op.drop_column("events", "centroid")
    op.add_column(
        "events",
        sa.Column("centroid", Vector(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "centroid")
    op.add_column(
        "events",
        sa.Column("centroid", Vector(1024), nullable=True),
    )
    op.drop_column("raw_articles", "embedding")
    op.add_column(
        "raw_articles",
        sa.Column("embedding", Vector(1024), nullable=True),
    )
