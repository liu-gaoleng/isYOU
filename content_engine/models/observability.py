"""阶段 D：可观测性 ORM 表（管线运行埋点落库）。

``PipelineRun`` 记录每一次管线运行（一次 run_all / 一条 Celery chain）的
逐阶段耗时、处理条数、成功/失败计数与 LLM 成本，使阶段埋点从「print + 内存
dict 随进程消失」变为可历史聚合的报表数据源。

设计要点（对齐 ops.py 的可移植约定）：
- 不使用 JSONB，逐阶段明细用通用 ``JSON`` 列（SQLite in-memory 单测可 create_all）；
- 一次运行一行：started_at / finished_at / duration_ms / status（running/success/failed）；
- ``stages`` 存各阶段 stats dict（含耗时与条数），``llm_cost`` 汇总本次 LLM 花费。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, IdMixin, TimestampMixin


class PipelineRun(IdMixin, TimestampMixin, Base):
    """一次管线运行的埋点记录。"""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_pipeline_runs_started_at", "started_at"),
        Index("ix_pipeline_runs_status", "status"),
    )

    # 触发方式：manual（run_all CLI）/ beat（Celery 周期）/ chain 等
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    # running / success / failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 逐阶段明细：{stage: {duration_ms, ...stats}}
    stages: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # 本次运行累计 LLM 成本（美元）
    llm_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # 失败时的错误信息（阶段名 + 异常）
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)


__all__ = ["PipelineRun"]
