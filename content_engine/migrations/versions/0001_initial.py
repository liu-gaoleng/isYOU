"""initial schema: sources / raw_articles / events / event_articles / event_contents / review_logs

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -------- 与 ORM 保持一致的常量 --------
EMBEDDING_DIM = 1024

ARTICLE_STATUS = ("raw", "cleaned", "classified", "clustered", "dropped")
EVENT_STATUS = (
    "clustered",
    "summarized",
    "scored",
    "reviewing",
    "published",
    "rejected",
)
SOURCE_LEVEL = ("S", "A", "B")
MODULE = ("tech", "finance", "ai", "macro")


def upgrade() -> None:
    # pgvector 扩展（init.sql 已建过；这里幂等创建以兼容非 docker 部署）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 1. 创建 4 个 Postgres ENUM 类型
    article_status = postgresql.ENUM(*ARTICLE_STATUS, name="article_status", create_type=True)
    event_status = postgresql.ENUM(*EVENT_STATUS, name="event_status", create_type=True)
    source_level = postgresql.ENUM(*SOURCE_LEVEL, name="source_level", create_type=True)
    module_enum = postgresql.ENUM(*MODULE, name="module", create_type=True)
    bind = op.get_bind()
    article_status.create(bind, checkfirst=True)
    event_status.create(bind, checkfirst=True)
    source_level.create(bind, checkfirst=True)
    module_enum.create(bind, checkfirst=True)

    # 2. sources
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "level",
            postgresql.ENUM(*SOURCE_LEVEL, name="source_level", create_type=False),
            nullable=False,
        ),
        sa.Column("type", sa.String(32), nullable=False, server_default="rss"),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column(
            "module",
            postgresql.ENUM(*MODULE, name="module", create_type=False),
            nullable=False,
        ),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("poll_interval_min", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # 3. raw_articles
    op.create_table(
        "raw_articles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "source_id",
            sa.BigInteger(),
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column(
            "module",
            postgresql.ENUM(*MODULE, name="module", create_type=False),
            nullable=True,
        ),
        sa.Column("cls_confidence", sa.Float(), nullable=True),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*ARTICLE_STATUS, name="article_status", create_type=False),
            nullable=False,
            server_default="raw",
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("url", name="uq_raw_articles_url"),
    )
    op.create_index("ix_raw_articles_status", "raw_articles", ["status"])
    op.create_index("ix_raw_articles_module_status", "raw_articles", ["module", "status"])
    op.create_index("ix_raw_articles_published_at", "raw_articles", ["published_at"])

    # 4. events
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "module",
            postgresql.ENUM(*MODULE, name="module", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("centroid", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("hotness", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_update", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*EVENT_STATUS, name="event_status", create_type=False),
            nullable=False,
            server_default="clustered",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_events_module_status", "events", ["module", "status"])
    op.create_index("ix_events_importance", "events", ["importance"])
    op.create_index("ix_events_last_update", "events", ["last_update"])

    # 5. event_articles
    op.create_table(
        "event_articles",
        sa.Column(
            "event_id",
            sa.BigInteger(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "article_id",
            sa.BigInteger(),
            sa.ForeignKey("raw_articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("similarity", sa.Float(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # 6. event_contents
    op.create_table(
        "event_contents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.BigInteger(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("why_matters", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "facts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("deep_content", sa.Text(), nullable=True),
        sa.Column("method", sa.String(32), nullable=False, server_default="extractive"),
        sa.Column("llm_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("event_id", "version", name="uq_event_contents_event_version"),
    )
    op.create_index("ix_event_contents_event_id", "event_contents", ["event_id"])

    # 7. review_logs
    op.create_table(
        "review_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            sa.BigInteger(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_review_logs_event_id", "review_logs", ["event_id"])
    op.create_index("ix_review_logs_action", "review_logs", ["action"])


def downgrade() -> None:
    op.drop_index("ix_review_logs_action", table_name="review_logs")
    op.drop_index("ix_review_logs_event_id", table_name="review_logs")
    op.drop_table("review_logs")

    op.drop_index("ix_event_contents_event_id", table_name="event_contents")
    op.drop_table("event_contents")

    op.drop_table("event_articles")

    op.drop_index("ix_events_last_update", table_name="events")
    op.drop_index("ix_events_importance", table_name="events")
    op.drop_index("ix_events_module_status", table_name="events")
    op.drop_table("events")

    op.drop_index("ix_raw_articles_published_at", table_name="raw_articles")
    op.drop_index("ix_raw_articles_module_status", table_name="raw_articles")
    op.drop_index("ix_raw_articles_status", table_name="raw_articles")
    op.drop_table("raw_articles")

    op.drop_table("sources")

    bind = op.get_bind()
    for enum_name in ("article_status", "event_status", "source_level", "module"):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
