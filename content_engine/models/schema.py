"""ORM 表定义（对应《内容管线方案》§9.2 六张核心表）。

设计要点：
1. 所有数据带 status 字段，支持按状态断点重跑；
2. raw_articles.embedding / events.centroid_vector 用 pgvector，先占位 1024 维（bge-large 量级），后续可按所选模型调整；
3. tags / summary / facts / sources 等列表字段统一用 JSONB，跨模块灵活；
4. 物理删除少用，过期内容靠 status=rejected/dropped 软淘汰。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, TimestampMixin
from .enums import ArticleStatus, EventStatus, Module, SourceLevel

# Embedding 维度按 bge-small-zh-v1.5 (512) 设定（阶段 1.3）；切模型时同步迁移
EMBEDDING_DIM = 512

# Postgres 原生 ENUM：与 Python Enum 对齐，便于 DB 端校验
_article_status_enum = PgEnum(ArticleStatus, name="article_status", create_type=True)
_event_status_enum = PgEnum(EventStatus, name="event_status", create_type=True)
_source_level_enum = PgEnum(SourceLevel, name="source_level", create_type=True)
_module_enum = PgEnum(Module, name="module", create_type=True)


# ----------------------------------------------------------------------------
# 1. sources —— 信源配置（可被后台增删改、调权重、停启用）
# ----------------------------------------------------------------------------
class Source(IdMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    level: Mapped[SourceLevel] = mapped_column(_source_level_enum, nullable=False)
    # type: rss / api / crawler / social
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="rss")
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    module: Mapped[Module] = mapped_column(_module_enum, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 采集频率（分钟），S 级 5~15，A/B 30~60；nil 表示用默认值
    poll_interval_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    articles: Mapped[list["RawArticle"]] = relationship(back_populates="source")


# ----------------------------------------------------------------------------
# 2. raw_articles —— 原始文章（含清洗后正文、embedding、分类结果）
# ----------------------------------------------------------------------------
class RawArticle(IdMixin, TimestampMixin, Base):
    __tablename__ = "raw_articles"
    __table_args__ = (
        # 采集幂等：同 url 不重复入库；空 url（少数源）退化用 raw_hash 兜底
        UniqueConstraint("url", name="uq_raw_articles_url"),
        Index("ix_raw_articles_status", "status"),
        Index("ix_raw_articles_module_status", "module", "status"),
        Index("ix_raw_articles_published_at", "published_at"),
        Index("ix_raw_articles_simhash", "simhash"),
    )

    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 原文摘要/正文的指纹，用于精确去重（SimHash 或简单 hash）
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # SimHash 64-bit hex（16 字符），用于近似去重（阶段 1.1）；空表示尚未生成
    simhash: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 语义向量；尚未生成时为空
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    # 分类结果（含规则兜底 / LLM）
    module: Mapped[Module | None] = mapped_column(_module_enum, nullable=True)
    cls_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[ArticleStatus] = mapped_column(
        _article_status_enum, nullable=False, default=ArticleStatus.raw
    )
    # 出错时记录最后一次失败原因，便于排障
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[Source] = relationship(back_populates="articles")
    event_links: Mapped[list["EventArticle"]] = relationship(back_populates="article")


# ----------------------------------------------------------------------------
# 3. events —— 事件簇（多源同事件聚合后的逻辑实体）
# ----------------------------------------------------------------------------
class Event(IdMixin, TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_module_status", "module", "status"),
        Index("ix_events_importance", "importance"),
        Index("ix_events_last_update", "last_update"),
    )

    module: Mapped[Module] = mapped_column(_module_enum, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    centroid: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    hotness: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # iOS 卡片流 / 详情页直读字段（阶段 1.4 摘要分级）：
    # - card_summary：≤120 中文字符，供卡片列表使用；
    # - detail_summary：300–500 中文字符，供详情页使用。
    # event_contents 表保留多版本结构化字段（summary / facts / sources），不冲突。
    card_summary: Mapped[str | None] = mapped_column(String(180), nullable=True)
    detail_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_update: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[EventStatus] = mapped_column(
        _event_status_enum, nullable=False, default=EventStatus.clustered
    )

    article_links: Mapped[list["EventArticle"]] = relationship(back_populates="event")
    contents: Mapped[list["EventContent"]] = relationship(back_populates="event")
    review_logs: Mapped[list["ReviewLog"]] = relationship(back_populates="event")


# ----------------------------------------------------------------------------
# 4. event_articles —— 事件 ↔ 文章 多对多归属
# ----------------------------------------------------------------------------
class EventArticle(TimestampMixin, Base):
    __tablename__ = "event_articles"

    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("raw_articles.id", ondelete="CASCADE"), primary_key=True
    )
    # 进簇时的相似度，便于复盘聚类
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)

    event: Mapped[Event] = relationship(back_populates="article_links")
    article: Mapped[RawArticle] = relationship(back_populates="event_links")


# ----------------------------------------------------------------------------
# 5. event_contents —— 摘要/解读多版本（含付费深度内容）
# ----------------------------------------------------------------------------
class EventContent(IdMixin, TimestampMixin, Base):
    __tablename__ = "event_contents"
    __table_args__ = (
        UniqueConstraint("event_id", "version", name="uq_event_contents_event_version"),
        Index("ix_event_contents_event_id", "event_id"),
    )

    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    # 三句摘要 / 关键事实（逐条挂来源序号）/ 引用源
    summary: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    why_matters: Mapped[str] = mapped_column(Text, nullable=False, default="")
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # 付费深度内容
    deep_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    # llm / extractive，便于区分兜底与正式产出
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="extractive")
    # 留痕：调用 LLM 时保存模型/温度/token 用量
    llm_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    event: Mapped[Event] = relationship(back_populates="contents")


# ----------------------------------------------------------------------------
# 6. review_logs —— 人工质检操作日志
# ----------------------------------------------------------------------------
class ReviewLog(IdMixin, TimestampMixin, Base):
    __tablename__ = "review_logs"
    __table_args__ = (
        Index("ix_review_logs_event_id", "event_id"),
        Index("ix_review_logs_action", "action"),
    )

    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String(64), nullable=False)
    # approve / reject / edit / merge / split / pin / unpin / push
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    event: Mapped[Event] = relationship(back_populates="review_logs")


# ----------------------------------------------------------------------------
# 7. source_health —— 信源健康记录（阶段 1.2）
# ----------------------------------------------------------------------------
class SourceHealth(IdMixin, TimestampMixin, Base):
    """每次 collect 一条记录，便于断流告警与 CMS 后台展示。

    设计要点：
    - 每次采集（无论成功/失败）都写一条，保留历史可追溯；
    - consecutive_failures 累计当前信源连续失败次数，达阈值由 collect 阶段 logging.WARNING；
    - 错误文本截断到 1024 字符，避免堆栈过长撑爆 DB。
    """

    __tablename__ = "source_health"
    __table_args__ = (
        Index("ix_source_health_source_fetched", "source_id", "fetched_at"),
        Index("ix_source_health_status", "status"),
    )

    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # success / failed / partial（解析成功但 0 条新内容也算 success，避免误报）
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 当前累计连续失败次数（成功时归零）
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    source: Mapped[Source] = relationship()


# ----------------------------------------------------------------------------
# 8. users —— 用户表（iOS-first，预留 Sign in with Apple 字段，阶段 1.5）
# ----------------------------------------------------------------------------
class User(IdMixin, TimestampMixin, Base):
    """iOS 客户端用户。本阶段仅预留表结构与字段，登录/鉴权端点放到后续阶段。

    设计要点：
    - apple_user_id：Sign in with Apple 返回的 sub（≤64 chars），唯一索引；
    - email：可空（用户可选择隐藏邮箱，由 Apple 中继提供 anonymous 邮箱）；
    - created_via：apple / wechat / test，便于后续多登录方式扩展。
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("apple_user_id", name="uq_users_apple_user_id"),
        Index("ix_users_email", "email"),
    )

    apple_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # apple / wechat / test
    created_via: Mapped[str] = mapped_column(String(16), nullable=False, default="apple")
    # 用户昵称（可由 Apple 首次登录时携带，仅首次返回）
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = [
    "Source",
    "RawArticle",
    "Event",
    "EventArticle",
    "EventContent",
    "ReviewLog",
    "SourceHealth",
    "User",
    "EMBEDDING_DIM",
]
