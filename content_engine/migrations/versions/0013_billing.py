"""add billing tables: iap_transactions + subscriptions (stage 3.2)

Revision ID: 0013_billing
Revises: 0012_user_collections
Create Date: 2026-06-19

阶段 3.2：会员订阅 / Apple IAP（StoreKit 2）服务端态。
- iap_transactions：每笔已验签交易的不可变审计记录（按 transaction_id 去重）；
- subscriptions：每用户当前订阅汇总态（按 user_id 一行，核销时 upsert）。

会员权益判定仍以 users.member_tier / member_expire_at 为准（0011 已建字段），
本两表是其背后的支付凭证与可追溯审计。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_billing"
down_revision: Union[str, None] = "0012_user_collections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "iap_transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("original_transaction_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("plan", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("environment", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("purchase_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("transaction_id", name="uq_iap_transactions_transaction_id"),
    )
    op.create_index("ix_iap_transactions_user_id", "iap_transactions", ["user_id"])
    op.create_index(
        "ix_iap_transactions_original_id", "iap_transactions", ["original_transaction_id"]
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "original_transaction_id", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column(
            "last_transaction_id", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("product_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("plan", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("environment", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("user_id", name="uq_subscriptions_user_id"),
    )
    op.create_index(
        "ix_subscriptions_status_expires", "subscriptions", ["status", "expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_status_expires", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("ix_iap_transactions_original_id", table_name="iap_transactions")
    op.drop_index("ix_iap_transactions_user_id", table_name="iap_transactions")
    op.drop_table("iap_transactions")
