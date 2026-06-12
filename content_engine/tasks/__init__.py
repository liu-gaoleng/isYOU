"""阶段 4.3：Celery 调度包。

模块组成：
- :mod:`celery_app`：Celery 应用实例（broker/backend 复用 Redis）+ beat 调度表；
- :mod:`pipeline_tasks`：把现有 stages.* 同步函数包成 Celery task，并用 chain 编排全链路。

启动方式（生产用 supervisor / k8s）：
    celery -A content_engine.tasks.celery_app worker -l info
    celery -A content_engine.tasks.celery_app beat   -l info
"""

from .celery_app import celery_app

__all__ = ["celery_app"]
