"""add member fields to users table (stage 3.2)

Revision ID: 0011_user_member_fields
Revises: 0010_pipeline_runs
Create Date: 2026-06-14

会员态落在登录用户（users 表）上，作为付费墙服务端裁剪的唯一判定依据：
- member_tier：free / member
- member_expire_at：会员到期时间（为空或已过期即非会员）
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_user_member_fields"
down_revision: Union[str, None] = "0010_pipeline_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "member_tier",
            sa.String(length=16),
            nullable=False,
            server_default="free",
        ),
    )
    op.add_column(
        "users",
        sa.Column("member_expire_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "member_expire_at")
    op.drop_column("users", "member_tier")
