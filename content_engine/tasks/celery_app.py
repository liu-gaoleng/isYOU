"""阶段 4.3：Celery 应用实例 + beat 调度表。

调度策略（对齐实施计划 §5 与方案 W4）：
- S 级信源 5–15min、A/B 级 30–60min 采集：collect 阶段内部已按 source 频率拉取，
  这里用一个 ``collect_and_process`` 周期任务（默认 15min）触发全链路 chain；
- 每日 cron 跑 HDBSCAN 离线复核（``daily_recluster``，默认 UTC 19:00 ≈ 北京 03:00）。

broker / backend 复用 Redis（不同 db），与榜单 Redis 物理同实例、逻辑隔离。
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from content_engine.config import settings
from content_engine.logging_config import configure_logging

configure_logging()

celery_app = Celery(
    "content_engine",
    broker=settings.celery.broker_url,
    backend=settings.celery.result_backend,
    include=["content_engine.tasks.pipeline_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # 单 worker 串行执行管线阶段，避免阶段间竞争同一批 status 数据
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # 任务结果保留 1 天，便于排障
    result_expires=86400,
)

# beat 周期调度表
celery_app.conf.beat_schedule = {
    # 每 15 分钟跑一轮全链路（采集→...→发布护栏）
    "collect-and-process-every-15min": {
        "task": "content_engine.tasks.pipeline_tasks.collect_and_process",
        "schedule": crontab(minute="*/15"),
    },
    # 每日离线复核（UTC 19:00）
    "daily-recluster": {
        "task": "content_engine.tasks.pipeline_tasks.daily_recluster",
        "schedule": crontab(hour=19, minute=0),
    },
}

__all__ = ["celery_app"]
