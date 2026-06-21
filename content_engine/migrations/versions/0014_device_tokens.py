"""add device_tokens table (stage 4.2)

Revision ID: 0014_device_tokens
Revises: 0013_billing
Create Date: 2026-06-21

阶段 4.2：APNs 推送的设备 token 注册表。
每台已授权推送的设备一行，按 token 唯一；同设备复登换户时直接 upsert user_id；
APNs 410/Unregistered 时回写 invalid_at 软删，避免重复尝试无效 token。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_device_tokens"
down_revision: Union[str, None] = "0013_billing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token", sa.String(length=255), nullable=False),
        sa.Column("bundle_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column(
            "environment", sa.String(length=16), nullable=False, server_default="production"
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalid_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token", name="uq_device_tokens_token"),
    )
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"])
    op.create_index("ix_device_tokens_invalid_at", "device_tokens", ["invalid_at"])


def downgrade() -> None:
    op.drop_index("ix_device_tokens_invalid_at", table_name="device_tokens")
    op.drop_index("ix_device_tokens_user_id", table_name="device_tokens")
    op.drop_table("device_tokens")
