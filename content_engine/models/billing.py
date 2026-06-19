"""阶段 3.2：会员订阅 / IAP 交易 ORM 表。

承载 Apple StoreKit 2 内购的服务端态（M3 §4.1 支付闭环的服务端基础）：
- IapTransaction —— 每笔已验签交易的不可变审计记录（按 Apple transaction_id 去重）；
- Subscription  —— 每个用户当前的订阅汇总态（按 user_id 一行，核销时 upsert）。

会员权益的唯一判定依据仍是 ``User.member_tier`` / ``member_expire_at``（见 deps.is_member）；
本两表是其背后的支付凭证与可追溯审计，便于退款处理、客服查证、到期巡检。

设计要点（对齐 ops.py：刻意只用可移植类型，使 SQLite in-memory 单测可 create_all）：
- 不使用 pgvector / JSONB，原始 payload 用通用 ``JSON``；
- user_id 用裸 ``BigInteger``（沿用 PushSetting 风格），不跨模块外键，避免 SQLite 单测麻烦；
- 时间统一 DateTime(timezone=True)，由 TimestampMixin 提供 created_at/updated_at。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, TimestampMixin


class IapTransaction(IdMixin, TimestampMixin, Base):
    """已验签的 StoreKit 2 交易（append-only 审计记录）。

    每笔交易（含每次自动续期）在 Apple 侧有唯一 transaction_id；以此去重，
    重复上送同一笔交易幂等。original_transaction_id 标识同一订阅链（续期共享）。
    """

    __tablename__ = "iap_transactions"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_iap_transactions_transaction_id"),
        Index("ix_iap_transactions_user_id", "user_id"),
        Index("ix_iap_transactions_original_id", "original_transaction_id"),
    )

    # Apple 交易唯一 id（每次续期不同）
    transaction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 同一订阅链的原始交易 id（续期共享，标识订阅身份）
    original_transaction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # App Store Connect 配置的商品 id（如 com.redu.app.member.monthly）
    product_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # 映射后的订阅档位（SubscriptionPlan 值：monthly/quarterly/yearly）
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    # Production / Sandbox（验签 payload 的 environment）
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    purchase_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # active / expired / refunded（SubscriptionStatus 值）
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # 解码后的交易 payload 原文（审计/排障用）
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Subscription(IdMixin, TimestampMixin, Base):
    """用户当前订阅汇总态（每用户一行，核销时按 user_id upsert）。

    始终保留该用户最新一笔有效订阅的状态：核销更新 expires_at/plan/status，
    定时巡检把已过期的置 expired 并降级 User.member_tier。
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_subscriptions_user_id"),
        Index("ix_subscriptions_status_expires", "status", "expires_at"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_transaction_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=""
    )
    # 当前生效交易 id（最近一次核销的 transaction_id）
    last_transaction_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    product_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    purchased_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 是否开启自动续订（来自 renewal info，仅供展示/续费引导）
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


__all__ = [
    "IapTransaction",
    "Subscription",
]
