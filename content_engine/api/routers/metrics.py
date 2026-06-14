"""阶段 D：可观测性报表接口。

供 CMS 后台 / 运营看板调用，聚合既有埋点数据出只读报表，让「print 随进程消失」
变为可历史回看的指标：

- GET /admin/metrics/overview        总览看板（产出量/质检/护栏/置信度/信源/成本）
- GET /admin/metrics/pipeline-runs   管线运行历史（逐次耗时/成败/LLM 成本）

数据源（全部为既有落库表）：
- events           —— 产出量、模块分布、按日发布量
- review_logs      —— 质检动作计数与通过率
- event_contents.llm_meta.guard —— 护栏拦截率
- raw_articles.cls_confidence   —— 分类置信度分布
- source_health    —— 信源健康
- pipeline_runs    —— 管线运行历史与 LLM 成本

鉴权：复用 review 的 ``require_admin``（静态 X-Admin-Token）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select

from content_engine.models import (
    Event,
    EventContent,
    EventStatus,
    PipelineRun,
    RawArticle,
    ReviewLog,
    SourceHealth,
    get_session,
)

from ..schemas import (
    ConfidenceBuckets,
    DailyCount,
    MetricsOverview,
    PipelineRunItem,
    SourceHealthCount,
)
from .review import require_admin

router = APIRouter(prefix="/admin", tags=["admin-metrics"])

# 视为「质检通过」的动作（用于通过率分母/分子）
_PASS_ACTIONS = {"approve"}
_REJECT_ACTIONS = {"reject", "merge"}


@router.get(
    "/metrics/overview",
    response_model=MetricsOverview,
    dependencies=[Depends(require_admin)],
)
def overview(
    days: int = Query(default=7, ge=1, le=90, description="统计窗口（天）"),
) -> MetricsOverview:
    """可观测性总览：聚合产出量/质检/护栏/置信度/信源/成本。"""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    with get_session() as s:
        events_total = s.execute(select(func.count(Event.id))).scalar_one()
        events_by_status = _count_by(s, select(Event.status, func.count(Event.id)).group_by(Event.status))
        events_by_module = _count_by(s, select(Event.module, func.count(Event.id)).group_by(Event.module))
        daily_published = _daily_published(s, since)

        review_action_counts = _count_by(
            s,
            select(ReviewLog.action, func.count(ReviewLog.id))
            .where(ReviewLog.created_at >= since)
            .group_by(ReviewLog.action),
        )
        review_pass_rate = _pass_rate(review_action_counts)

        guard_checked, guard_intercepted = _guard_stats(s)
        guard_rate = round(guard_intercepted / guard_checked, 4) if guard_checked else 0.0

        confidence = _confidence_buckets(s)
        source_health = _source_health(s)

        llm_cost_total = s.execute(
            select(func.coalesce(func.sum(PipelineRun.llm_cost), 0.0)).where(
                PipelineRun.started_at >= since
            )
        ).scalar_one()
        runs_total = s.execute(
            select(func.count(PipelineRun.id)).where(PipelineRun.started_at >= since)
        ).scalar_one()
        runs_success = s.execute(
            select(func.count(PipelineRun.id)).where(
                PipelineRun.started_at >= since, PipelineRun.status == "success"
            )
        ).scalar_one()
        pipeline_success_rate = round(runs_success / runs_total, 4) if runs_total else 0.0

        return MetricsOverview(
            generated_at=now,
            window_days=days,
            events_total=int(events_total),
            events_by_status=events_by_status,
            events_by_module=events_by_module,
            daily_published=daily_published,
            review_action_counts=review_action_counts,
            review_pass_rate=review_pass_rate,
            guard_checked=guard_checked,
            guard_intercepted=guard_intercepted,
            guard_interception_rate=guard_rate,
            classification_confidence=confidence,
            source_health=source_health,
            llm_cost_total=round(float(llm_cost_total), 6),
            pipeline_runs_total=int(runs_total),
            pipeline_success_rate=pipeline_success_rate,
        )


@router.get(
    "/metrics/pipeline-runs",
    response_model=list[PipelineRunItem],
    dependencies=[Depends(require_admin)],
)
def pipeline_runs(
    limit: int = Query(default=20, ge=1, le=200),
    status: str | None = Query(default=None, description="可选按 running/success/failed 过滤"),
) -> list[PipelineRunItem]:
    """管线运行历史：最近在前。"""
    stmt = select(PipelineRun).order_by(desc(PipelineRun.started_at)).limit(limit)
    if status:
        stmt = stmt.where(PipelineRun.status == status)
    with get_session() as s:
        rows = s.execute(stmt).scalars().all()
        return [
            PipelineRunItem(
                id=r.id,
                trigger=r.trigger,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                duration_ms=r.duration_ms,
                llm_cost=r.llm_cost,
                error=r.error,
                stages=r.stages or {},
            )
            for r in rows
        ]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _count_by(s, stmt) -> dict[str, int]:
    """执行 (key, count) 分组查询，把 Enum/标量 key 归一成字符串。"""
    out: dict[str, int] = {}
    for key, cnt in s.execute(stmt).all():
        out[getattr(key, "value", key)] = int(cnt)
    return out


def _daily_published(s, since: datetime) -> list[DailyCount]:
    """按 last_update 日期统计已发布事件数（窗口内）。"""
    rows = s.execute(
        select(Event.last_update).where(
            Event.status == EventStatus.published, Event.last_update >= since
        )
    ).scalars().all()
    buckets: dict[str, int] = {}
    for ts in rows:
        if ts is None:
            continue
        day = ts.date().isoformat()
        buckets[day] = buckets.get(day, 0) + 1
    return [DailyCount(date=d, count=c) for d, c in sorted(buckets.items())]


def _pass_rate(action_counts: dict[str, int]) -> float:
    passed = sum(action_counts.get(a, 0) for a in _PASS_ACTIONS)
    rejected = sum(action_counts.get(a, 0) for a in _REJECT_ACTIONS)
    denom = passed + rejected
    return round(passed / denom, 4) if denom else 0.0


def _guard_stats(s) -> tuple[int, int]:
    """护栏拦截率：扫描 event_contents.llm_meta.guard，统计有护栏检查的内容与命中数。"""
    rows = s.execute(select(EventContent.llm_meta)).scalars().all()
    checked = 0
    intercepted = 0
    for meta in rows:
        if not meta:
            continue
        guard = meta.get("guard")
        if not isinstance(guard, dict):
            continue
        checked += 1
        if guard.get("violations"):
            intercepted += 1
    return checked, intercepted


def _confidence_buckets(s) -> ConfidenceBuckets:
    rows = s.execute(select(RawArticle.cls_confidence)).scalars().all()
    b = ConfidenceBuckets()
    for c in rows:
        if c is None:
            b.unknown += 1
        elif c >= 0.8:
            b.high += 1
        elif c >= 0.5:
            b.mid += 1
        else:
            b.low += 1
    return b


def _source_health(s) -> SourceHealthCount:
    """信源健康：状态计数 + 最近一次记录仍连续失败的信源数。"""
    status_counts = _count_by(
        s, select(SourceHealth.status, func.count(SourceHealth.id)).group_by(SourceHealth.status)
    )
    # 每个 source 取最近一条，统计 consecutive_failures > 0 的数量
    latest = (
        select(
            SourceHealth.source_id,
            func.max(SourceHealth.fetched_at).label("mx"),
        )
        .group_by(SourceHealth.source_id)
        .subquery()
    )
    rows = s.execute(
        select(SourceHealth.consecutive_failures)
        .join(
            latest,
            (SourceHealth.source_id == latest.c.source_id)
            & (SourceHealth.fetched_at == latest.c.mx),
        )
    ).scalars().all()
    failing = sum(1 for c in rows if (c or 0) > 0)
    return SourceHealthCount(status_counts=status_counts, failing_sources=failing)
