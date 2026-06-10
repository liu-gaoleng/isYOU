"""SQLAlchemy 2.0 DeclarativeBase 与公共 mixin。

约定：
- 所有表继承 Base；
- 所有业务表带 created_at / updated_at（TimestampMixin）；
- 主键统一用 BigInteger 自增，避免后期分表/迁移踩坑；
  在 SQLite（仅离线单测使用）上降级为 Integer 以确保 autoincrement 正确工作，
  PG 端仍编译为 bigint，与既有 BIGSERIAL 行为一致。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的根基类。"""


class TimestampMixin:
    """统一的创建/更新时间字段。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class IdMixin:
    """统一的 BigInt 自增主键。

    PG/MySQL 等：BigInteger（实际即 BIGSERIAL）；
    SQLite：变体为 Integer，使 SQLAlchemy 编译为 INTEGER PRIMARY KEY AUTOINCREMENT，
            否则 BigInteger 在 SQLite 上不会自动赋值，导致 NOT NULL 报错。
    """

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
