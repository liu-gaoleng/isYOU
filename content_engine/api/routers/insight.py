"""热点透视端点（会员专属）。

- ``POST /event/{id}/insight``：触发生成（幂等：ready 直返、进行中不重复入队、
  failed/stale 允许重投）；
- ``GET  /event/{id}/insight``：查询状态/结果（无记录返回 ``status="none"``，不走 404）。

生成逻辑与状态机见 ``services/insight.py`` / ``tasks/insight_tasks.py``。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from content_engine.api.deps import require_member
from content_engine.api.schemas import EventInsight, InsightSections
from content_engine.config import settings
from content_engine.models import (
    PUBLIC_EVENT_STATUSES,
    Event,
    EventAnalysis,
    User,
    get_session,
)
from content_engine.services.insight import INSIGHT_DISCLAIMER, claim_for_generation
from content_engine.tasks.insight_tasks import generate_insight_task

router = APIRouter(tags=["insight"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enqueue(event_id: int) -> None:
    """独立函数便于测试 monkeypatch；调用方必须在 session commit 之后再调。"""
    generate_insight_task.delay(event_id)


def _get_visible_event(session, event_id: int) -> Event:
    """与详情页同口径：仅 published 可见，否则 404。"""
    ev = session.get(Event, event_id)
    if ev is None or ev.status not in PUBLIC_EVENT_STATUSES:
        raise HTTPException(status_code=404, detail="event not found")
    return ev


def _to_insight(row: EventAnalysis) -> EventInsight:
    sections = None
    if row.status == "ready" and row.sections:
        sections = InsightSections(**row.sections)
    return EventInsight(
        event_id=row.event_id,
        status=row.status,
        sections=sections,
        disclaimer=INSIGHT_DISCLAIMER if sections is not None else None,
        error=row.error if row.status == "failed" else None,
        generated_at=row.generated_at,
    )


@router.post("/event/{event_id}/insight", response_model=EventInsight)
def trigger_insight(event_id: int, user: User = Depends(require_member)) -> EventInsight:
    """触发生成。重复触发幂等：ready 直接返回，进行中不入队，failed/stale 重投。"""
    with get_session() as s:
        _get_visible_event(s, event_id)
        row, should_enqueue = claim_for_generation(s, event_id, _utcnow())
        if should_enqueue and not settings.llm.enabled:
            # 需要新生成但 LLM 未配置：抛错回滚（claim 建的 pending 行不会落库）
            raise HTTPException(status_code=503, detail={"code": "insight_unavailable"})
        payload = _to_insight(row)
    # commit 之后再入队，避免 worker 先于 commit 读到空行
    if should_enqueue:
        _enqueue(event_id)
    return payload


@router.get("/event/{event_id}/insight", response_model=EventInsight)
def get_insight(event_id: int, user: User = Depends(require_member)) -> EventInsight:
    """查询状态/结果。无记录 → status="none"（会员重进页面的正常恢复路径）。"""
    with get_session() as s:
        _get_visible_event(s, event_id)
        row = s.execute(
            select(EventAnalysis).where(EventAnalysis.event_id == event_id)
        ).scalar_one_or_none()
        if row is None:
            return EventInsight(event_id=event_id, status="none")
        return _to_insight(row)


__all__ = ["router"]
