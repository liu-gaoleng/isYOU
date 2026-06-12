"""add operational tables for mock_server data backing (stage 4.4)

Revision ID: 0008_ops_tables
Revises: 0007_event_review_fields
Create Date: 2026-06-12

阶段 4.4：mock_server 真实数据化。新增承载运营态数据的表：
app_users / app_orders / reports / report_purchases / push_records /
digest_config / admin_members / favorites / reading_history。

刻意只用可移植类型（JSON 而非 JSONB，无 pgvector），与 models/ops.py 对齐。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_ops_tables"
down_revision: Union[str, None] = "0007_event_review_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def _ts_cols() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    ]


def upgrade() -> None:
    # app_users
    op.create_table(
        "app_users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        *_ts_cols(),
        sa.Column("biz_id", sa.String(length=32), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("nick", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="free"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("member_expire", sa.String(length=16), nullable=False, server_default=""),
        sa.Column("total_paid", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("biz_id", name="uq_app_users_biz_id"),
    )
    op.create_index("ix_app_users_tier_status", "app_users", ["tier", "status"])

    # app_orders
    op.create_table(
        "app_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        *_ts_cols(),
        sa.Column("biz_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("plan", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("biz_id", name="uq_app_orders_biz_id"),
    )
    op.create_index("ix_app_orders_user_id", "app_orders", ["user_id"])

    # reports
    op.create_table(
        "reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        *_ts_cols(),
        sa.Column("biz_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("module", sa.String(length=16), nullable=False, server_default="tech"),
        sa.Column("pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("member_free", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("toc", sa.JSON(), nullable=False),
        sa.UniqueConstraint("biz_id", name="uq_reports_biz_id"),
    )

    # report_purchases
    op.create_table(
        "report_purchases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        *_ts_cols(),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("report_biz_id", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "token", "report_biz_id", name="uq_report_purchase_token_report"
        ),
    )
    op.create_index("ix_report_purchases_token", "report_purchases", ["token"])

    # push_records
    op.create_table(
        "push_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        *_ts_cols(),
        sa.Column("biz_id", sa.String(length=32), nullable=False),
        sa.Column("event_ref", sa.String(length=64), nullable=True),
        sa.Column("type", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("title", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("audience", sa.String(length=16), nullable=False, server_default="all"),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opened", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_ids", sa.JSON(), nullable=False),
        sa.UniqueConstraint("biz_id", name="uq_push_records_biz_id"),
    )
    op.create_index("ix_push_records_pushed_at", "push_records", ["pushed_at"])

    # digest_config (single row)
    op.create_table(
        "digest_config",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        *_ts_cols(),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("send_time", sa.String(length=8), nullable=False, server_default="08:00"),
        sa.Column("audience", sa.String(length=16), nullable=False, server_default="all"),
        sa.Column("modules", sa.JSON(), nullable=False),
        sa.Column("top_n", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("title_template", sa.String(length=256), nullable=False),
    )

    # admin_members
    op.create_table(
        "admin_members",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        *_ts_cols(),
        sa.Column("biz_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="viewer"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("biz_id", name="uq_admin_members_biz_id"),
    )

    # favorites
    op.create_table(
        "favorites",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        *_ts_cols(),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("token", "event_id", name="uq_favorites_token_event"),
    )
    op.create_index("ix_favorites_token", "favorites", ["token"])

    # reading_history
    op.create_table(
        "reading_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        *_ts_cols(),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_reading_history_token_viewed", "reading_history", ["token", "viewed_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_reading_history_token_viewed", table_name="reading_history")
    op.drop_table("reading_history")
    op.drop_index("ix_favorites_token", table_name="favorites")
    op.drop_table("favorites")
    op.drop_table("admin_members")
    op.drop_table("digest_config")
    op.drop_index("ix_push_records_pushed_at", table_name="push_records")
    op.drop_table("push_records")
    op.drop_index("ix_report_purchases_token", table_name="report_purchases")
    op.drop_table("report_purchases")
    op.drop_table("reports")
    op.drop_index("ix_app_orders_user_id", table_name="app_orders")
    op.drop_table("app_orders")
    op.drop_index("ix_app_users_tier_status", table_name="app_users")
    op.drop_table("app_users")
