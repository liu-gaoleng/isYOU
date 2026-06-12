"""阶段 4.3 单测：Celery 应用配置 + chain 编排 + task 注册。

不真起 worker/broker，只校验：
- celery_app 基础配置（序列化 / UTC / prefetch / acks_late）；
- beat_schedule 两条目存在且指向正确 task 名；
- pipeline_chain() 返回含 7 个 stage 的 chain，顺序正确；
- 全部 stage task / 入口 task 名注册在 celery_app.tasks 中；
- stage task 包装函数正确委派给 stages.*.run（用 monkeypatch 验证）。
"""

from __future__ import annotations

from content_engine.tasks import celery_app
from content_engine.tasks import pipeline_tasks as pt

EXPECTED_STAGE_TASKS = [
    "content_engine.tasks.pipeline_tasks.collect_stage",
    "content_engine.tasks.pipeline_tasks.clean_stage",
    "content_engine.tasks.pipeline_tasks.classify_stage",
    "content_engine.tasks.pipeline_tasks.cluster_stage",
    "content_engine.tasks.pipeline_tasks.summarize_stage",
    "content_engine.tasks.pipeline_tasks.score_stage",
    "content_engine.tasks.pipeline_tasks.publish_stage",
]


def test_celery_conf_basics():
    conf = celery_app.conf
    assert conf.task_serializer == "json"
    assert conf.accept_content == ["json"]
    assert conf.enable_utc is True
    assert conf.timezone == "UTC"
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_acks_late is True


def test_beat_schedule_entries():
    schedule = celery_app.conf.beat_schedule
    assert "collect-and-process-every-15min" in schedule
    assert "daily-recluster" in schedule
    assert (
        schedule["collect-and-process-every-15min"]["task"]
        == "content_engine.tasks.pipeline_tasks.collect_and_process"
    )
    assert (
        schedule["daily-recluster"]["task"]
        == "content_engine.tasks.pipeline_tasks.daily_recluster"
    )


def test_all_tasks_registered():
    registered = set(celery_app.tasks.keys())
    for name in EXPECTED_STAGE_TASKS:
        assert name in registered
    assert "content_engine.tasks.pipeline_tasks.collect_and_process" in registered
    assert "content_engine.tasks.pipeline_tasks.daily_recluster" in registered


def test_pipeline_chain_structure():
    ch = pt.pipeline_chain()
    task_names = [sig.task for sig in ch.tasks]
    assert task_names == EXPECTED_STAGE_TASKS


def test_pipeline_chain_uses_immutable_signatures():
    ch = pt.pipeline_chain()
    # immutable signature 不接收上一步返回值
    assert all(sig.immutable for sig in ch.tasks)


def test_collect_stage_delegates(monkeypatch):
    called = {}

    def fake_run():
        called["collect"] = True
        return {"ok": 1}

    monkeypatch.setattr(pt.collect, "run", fake_run)
    assert pt.collect_stage.run() == {"ok": 1}
    assert called["collect"] is True


def test_daily_recluster_delegates(monkeypatch):
    monkeypatch.setattr(pt.recluster, "run", lambda: {"reclustered": 3})
    assert pt.daily_recluster.run() == {"reclustered": 3}
