"""add singleton guard column + unique constraint to digest_config (stage 4.4 hardening)

Revision ID: 0009_digest_singleton
Revises: 0008_ops_tables
Create Date: 2026-06-12

为 digest_config 增加 singleton 守卫列（固定 True）+ 唯一约束，
从 DB 层强制该配置表至多一行，防止并发或误插出现多行。
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_digest_singleton"
down_revision: Union[str, None] = "0008_ops_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 新增 singleton 列，存量行回填 True
    op.add_column(
        "digest_config",
        sa.Column("singleton", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_unique_constraint(
        "uq_digest_config_singleton", "digest_config", ["singleton"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_digest_config_singleton", "digest_config", type_="unique")
    op.drop_column("digest_config", "singleton")
