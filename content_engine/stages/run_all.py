"""管线编排：按 status 顺序串起阶段，并落库每次运行的可观测埋点（阶段 D）。

提供同步顺序入口，便于本地端到端验证：

    python -m content_engine.stages.run_all

每次运行写一条 ``pipeline_runs``：逐阶段耗时/条数、累计 LLM 成本、成败状态，
作为可观测性报表（metrics API）的数据源。DB 不可用时不阻断管线（best-effort）。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from content_engine.logging_config import get_logger

from . import classify, clean, cluster, collect, publish, score, summarize

_logger = get_logger(__name__)

# 阶段执行顺序（名称 → run 函数）
_STAGES = [
    ("collect", collect.run),
    ("clean", clean.run),
    ("classify", classify.run),
    ("cluster", cluster.run),
    ("summarize", summarize.run),
    ("score", score.run),
    ("publish", publish.run),
]


def _create_run(trigger: str, started_at: datetime):
    """创建 running 状态的 PipelineRun 行，返回其 id（DB 不可用返回 None）。"""
    try:
        from content_engine.models import PipelineRun, get_session

        with get_session() as s:
            run = PipelineRun(
                trigger=trigger, status="running", started_at=started_at, stages={}
            )
            s.add(run)
            s.flush()
            return run.id
    except Exception as exc:  # noqa: BLE001
        _logger.warning("无法创建 pipeline_runs 记录（管线继续）：%s", exc)
        return None


def _finalize_run(run_id, *, status, stages, llm_cost, started_at, error=None):
    """回填 PipelineRun 的终态（耗时/明细/成本/状态）。best-effort。"""
    if run_id is None:
        return
    try:
        from content_engine.models import PipelineRun, get_session

        finished = datetime.now(timezone.utc)
        with get_session() as s:
            run = s.get(PipelineRun, run_id)
            if run is None:
                return
            run.status = status
            run.finished_at = finished
            run.duration_ms = int((finished - started_at).total_seconds() * 1000)
            run.stages = stages
            run.llm_cost = round(llm_cost, 6)
            run.error = error
    except Exception as exc:  # noqa: BLE001
        _logger.warning("无法回填 pipeline_runs 记录：%s", exc)


def run_all(trigger: str = "manual") -> dict:
    t0 = time.time()
    started_at = datetime.now(timezone.utc)
    _logger.info("「热读」内容引擎 —— 顺序编排执行开始（trigger=%s）", trigger)

    run_id = _create_run(trigger, started_at)
    stages_stats: dict[str, dict] = {}
    llm_cost = 0.0
    status = "success"
    error = None

    for name, fn in _STAGES:
        stage_t0 = time.time()
        try:
            stat = fn() or {}
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error = f"{name}: {type(exc).__name__}: {exc}"
            _logger.exception("[run_all] 阶段 %s 失败，中断管线", name)
            stages_stats[name] = {"duration_ms": int((time.time() - stage_t0) * 1000),
                                  "error": str(exc)}
            break
        stat["duration_ms"] = int((time.time() - stage_t0) * 1000)
        stages_stats[name] = stat
        # 汇总 LLM 成本（目前 classify 阶段会上报 llm_cost）
        llm_cost += float(stat.get("llm_cost") or 0.0)

    _finalize_run(run_id, status=status, stages=stages_stats, llm_cost=llm_cost,
                  started_at=started_at, error=error)
    _logger.info("[run_all] 完成 status=%s 耗时 %.1fs LLM成本 $%.4f",
                 status, time.time() - t0, llm_cost)
    return stages_stats


if __name__ == "__main__":
    run_all()
