"""阶段 4.3：管线 Celery 任务 + chain 编排。

把现有 ``stages.*`` 同步 ``run()`` 函数包成 Celery task，再用 ``chain`` 串成全链路：
    collect → clean → classify → cluster → summarize → score → publish

设计要点：
- 每个 stage task 是**幂等**的：内部按 status 取待处理数据，可安全重试；
- 阶段间无需传递数据（各 stage 读写 DB 的 status 字段衔接），chain 仅控制执行顺序，
  因此用 ``si()``（immutable signature）避免把上一步返回值塞进下一步参数；
- ``collect_and_process`` 是 beat 周期入口，触发一次完整 chain；
- ``daily_recluster`` 独立日级任务，跑 HDBSCAN 离线复核（只打标，不改归属）。
"""

from __future__ import annotations

from celery import chain

from content_engine.stages import (
    classify,
    clean,
    cluster,
    collect,
    publish,
    recluster,
    score,
    summarize,
)

from .celery_app import celery_app


@celery_app.task(name="content_engine.tasks.pipeline_tasks.collect_stage")
def collect_stage() -> dict:
    return collect.run()


@celery_app.task(name="content_engine.tasks.pipeline_tasks.clean_stage")
def clean_stage() -> dict:
    return clean.run()


@celery_app.task(name="content_engine.tasks.pipeline_tasks.classify_stage")
def classify_stage() -> dict:
    return classify.run()


@celery_app.task(name="content_engine.tasks.pipeline_tasks.cluster_stage")
def cluster_stage() -> dict:
    return cluster.run()


@celery_app.task(name="content_engine.tasks.pipeline_tasks.summarize_stage")
def summarize_stage() -> dict:
    return summarize.run()


@celery_app.task(name="content_engine.tasks.pipeline_tasks.score_stage")
def score_stage() -> dict:
    return score.run()


@celery_app.task(name="content_engine.tasks.pipeline_tasks.publish_stage")
def publish_stage() -> dict:
    return publish.run()


def pipeline_chain():
    """构造全链路 chain（immutable signatures，阶段间不传值）。"""
    return chain(
        collect_stage.si(),
        clean_stage.si(),
        classify_stage.si(),
        cluster_stage.si(),
        summarize_stage.si(),
        score_stage.si(),
        publish_stage.si(),
    )


@celery_app.task(name="content_engine.tasks.pipeline_tasks.collect_and_process")
def collect_and_process() -> str:
    """beat 周期入口：异步触发一次全链路 chain，返回 chain 的 root id。"""
    result = pipeline_chain().apply_async()
    return result.id


@celery_app.task(name="content_engine.tasks.pipeline_tasks.daily_recluster")
def daily_recluster() -> dict:
    """每日 HDBSCAN 离线复核（只打标 needs_split / suggested_merge_id）。"""
    return recluster.run()


__all__ = [
    "collect_stage",
    "clean_stage",
    "classify_stage",
    "cluster_stage",
    "summarize_stage",
    "score_stage",
    "publish_stage",
    "pipeline_chain",
    "collect_and_process",
    "daily_recluster",
]
