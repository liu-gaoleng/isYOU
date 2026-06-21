"""阶段 4.4：运营态 ORM 表（mock_server 真实数据化）。

这些表承载 mock_server 之前用内存 dict 维护的运营态数据，使其落到真实 DB：
- AppUser / AppOrder      —— C 端 App 用户与付费订单
- Report / ReportPurchase —— 付费报告与已购记录
- PushRecord              —— 推送历史（含触达/打开指标）
- DigestConfig            —— 定时早报配置（单行）
- AdminMember             —— 后台运营成员（RBAC 角色绑定）
- Favorite / ReadingHistory —— C 端收藏与阅读历史

设计要点（对齐既有 schema.py 风格，但刻意只用可移植类型）：
- 不使用 pgvector / JSONB，列表字段用通用 ``JSON``，使 SQLite in-memory 单测可直接 create_all；
- 主键沿用 IdMixin（PG BIGSERIAL / SQLite INTEGER 自增），业务可读 id（如 au_/rpt_）作为额外唯一列；
- 时间统一 DateTime(timezone=True)，由 TimestampMixin 提供 created_at/updated_at。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, IdMixin, TimestampMixin


# ----------------------------------------------------------------------------
# C 端 App 用户 + 付费订单
# ----------------------------------------------------------------------------
class AppUser(IdMixin, TimestampMixin, Base):
    """C 端 App 用户运营记录（手机号注册 / 会员态 / 付费汇总）。"""

    __tablename__ = "app_users"
    __table_args__ = (
        UniqueConstraint("biz_id", name="uq_app_users_biz_id"),
        Index("ix_app_users_tier_status", "tier", "status"),
    )

    # 业务可读 id（如 au_1），供前端/CMS 使用
    biz_id: Mapped[str] = mapped_column(String(32), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    nick: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # free / member
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="free")
    # active / banned
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 会员到期日（YYYY-MM-DD 文本，空表示非会员）
    member_expire: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    total_paid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    orders: Mapped[list["AppOrder"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AppOrder(IdMixin, TimestampMixin, Base):
    """C 端会员/报告订单。"""

    __tablename__ = "app_orders"
    __table_args__ = (
        UniqueConstraint("biz_id", name="uq_app_orders_biz_id"),
        Index("ix_app_orders_user_id", "user_id"),
    )

    biz_id: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False
    )
    plan: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[AppUser] = relationship(back_populates="orders")


# ----------------------------------------------------------------------------
# 付费报告 + 已购记录
# ----------------------------------------------------------------------------
class Report(IdMixin, TimestampMixin, Base):
    """付费深度报告。"""

    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("biz_id", name="uq_reports_biz_id"),)

    biz_id: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    # tech / finance / ai / macro
    module: Mapped[str] = mapped_column(String(16), nullable=False, default="tech")
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 会员是否免费（否则会员 8 折）
    member_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 目录章节列表
    toc: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class ReportPurchase(IdMixin, TimestampMixin, Base):
    """报告已购记录（按 token 区分用户，与 mock 联调态对齐）。"""

    __tablename__ = "report_purchases"
    __table_args__ = (
        UniqueConstraint("token", "report_biz_id", name="uq_report_purchase_token_report"),
        Index("ix_report_purchases_token", "token"),
    )

    token: Mapped[str] = mapped_column(String(64), nullable=False)
    report_biz_id: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ----------------------------------------------------------------------------
# 推送历史
# ----------------------------------------------------------------------------
class PushRecord(IdMixin, TimestampMixin, Base):
    """推送历史（手动推送 / 每日早报），含触达与打开指标。"""

    __tablename__ = "push_records"
    __table_args__ = (
        UniqueConstraint("biz_id", name="uq_push_records_biz_id"),
        Index("ix_push_records_pushed_at", "pushed_at"),
    )

    biz_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # 关联事件（每日早报为空），存事件业务 id 文本（evt_ / int 皆可）
    event_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # manual / daily
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    # all / member / free
    audience: Mapped[str] = mapped_column(String(16), nullable=False, default="all")
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 每日早报纳入的事件 id 列表
    event_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


# ----------------------------------------------------------------------------
# 定时早报配置（单行）
# ----------------------------------------------------------------------------
class DigestConfig(IdMixin, TimestampMixin, Base):
    """每日早报推送配置，全表仅一行（由 singleton 唯一约束强制单例）。"""

    __tablename__ = "digest_config"
    __table_args__ = (
        UniqueConstraint("singleton", name="uq_digest_config_singleton"),
    )

    # 单例守卫：固定为 True，配合唯一约束保证全表至多一行（防并发/误插多行）
    singleton: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    send_time: Mapped[str] = mapped_column(String(8), nullable=False, default="08:00")
    # all / member / free
    audience: Mapped[str] = mapped_column(String(16), nullable=False, default="all")
    modules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    top_n: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    title_template: Mapped[str] = mapped_column(
        String(256), nullable=False, default="每日早报 · {date} | 今日 {count} 条要闻"
    )


# ----------------------------------------------------------------------------
# 后台运营成员（RBAC 角色绑定；角色权限矩阵仍由代码常量定义）
# ----------------------------------------------------------------------------
class AdminMember(IdMixin, TimestampMixin, Base):
    """后台运营成员。role 对应代码中的 ROLE_PERMS（admin/auditor/operator/viewer）。"""

    __tablename__ = "admin_members"
    __table_args__ = (UniqueConstraint("biz_id", name="uq_admin_members_biz_id"),)

    biz_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ----------------------------------------------------------------------------
# C 端收藏 / 阅读历史
# 两套主体并存：
# - token：mock_server 联调态（CMS/原型页用 Bearer token 模拟用户）；
# - user_id：生产 C 端 API（阶段 3.4），由 JWT 解出的真实登录用户 id。
# 两列均可空，按写入方填其一；用户维度各自唯一约束/索引，互不干扰。
# ----------------------------------------------------------------------------
class Favorite(IdMixin, TimestampMixin, Base):
    """C 端收藏（token 或 user_id ↔ 事件）。"""

    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("token", "event_id", name="uq_favorites_token_event"),
        UniqueConstraint("user_id", "event_id", name="uq_favorites_user_event"),
        Index("ix_favorites_token", "token"),
        Index("ix_favorites_user_id", "user_id"),
    )

    token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ReadingHistory(IdMixin, TimestampMixin, Base):
    """C 端阅读历史（token 或 user_id ↔ 事件，最近在前）。"""

    __tablename__ = "reading_history"
    __table_args__ = (
        Index("ix_reading_history_token_viewed", "token", "viewed_at"),
        Index("ix_reading_history_user_viewed", "user_id", "viewed_at"),
    )

    token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PushSetting(IdMixin, TimestampMixin, Base):
    """C 端推送设置（每个登录用户一行，阶段 3.4）。"""

    __tablename__ = "push_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_push_settings_user_id"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 每日早报推送
    daily_push: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 推送时间（HH:MM）
    push_time: Mapped[str] = mapped_column(String(8), nullable=False, default="08:00")
    # 突发要闻推送
    breaking_push: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ----------------------------------------------------------------------------
# 阶段 4.2：APNs 设备 token 注册表
# 每台已授权推送的设备一行；登出/卸载时删除；同 token 复登换户时按唯一约束升级。
# ----------------------------------------------------------------------------
class DeviceToken(IdMixin, TimestampMixin, Base):
    """APNs 设备 token 注册表。

    - ``token``：APNs 下发的 64 字节 hex 字符串，全局唯一（同设备复登换户时按
      唯一约束 upsert 至最新 user_id）。
    - ``environment``：``sandbox`` / ``production``，决定推送下发的 APNs 主机。
    - ``bundle_id``：客户端自报，便于多 bundle 共用一个后端时分流。
    - ``last_seen_at``：每次客户端启动注册时更新，用于清理长期失活设备。
    - ``invalid_at``：APNs 410/Unregistered 时回写，作为软删墓碑。
    """

    __tablename__ = "device_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_device_tokens_token"),
        Index("ix_device_tokens_user_id", "user_id"),
        Index("ix_device_tokens_invalid_at", "invalid_at"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token: Mapped[str] = mapped_column(String(255), nullable=False)
    bundle_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="production")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "AppUser",
    "AppOrder",
    "Report",
    "ReportPurchase",
    "PushRecord",
    "DigestConfig",
    "AdminMember",
    "Favorite",
    "ReadingHistory",
    "PushSetting",
    "DeviceToken",
]
