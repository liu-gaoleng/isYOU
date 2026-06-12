"""每日简报 / 事件详情 / 信息流 三个核心只读接口。"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, time, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select

from content_engine.models import (
    Event,
    EventContent,
    EventStatus,
    Module,
    get_session,
)
from content_engine.services import ranking

from ..schemas import EventCard, EventDetail, EventSourceItem, FeedPage

router = APIRouter(tags=["events"])

# 信息流可见状态：summarized / scored / published（已生成可读内容即可对外）
_VISIBLE_STATUSES = (
    EventStatus.summarized,
    EventStatus.scored,
    EventStatus.published,
)


def _latest_content(ev: Event) -> EventContent | None:
    """挑当前事件最新 version 的 EventContent；无则 None。"""
    if not ev.contents:
        return None
    return max(ev.contents, key=lambda c: c.version)


def _to_card(ev: Event) -> EventCard:
    content = _latest_content(ev)
    return EventCard(
        id=ev.id,
        module=ev.module.value,
        title=(content.title if content else None),
        card_summary=ev.card_summary,
        importance=ev.importance,
        hotness=ev.hotness,
        source_count=ev.source_count,
        tags=list(ev.tags or []),
        last_update=ev.last_update,
    )


def _to_detail(ev: Event) -> EventDetail:
    content = _latest_content(ev)
    sources_payload: list[EventSourceItem] = []
    if content and content.sources:
        for item in content.sources:
            try:
                sources_payload.append(
                    EventSourceItem(
                        name=item.get("name", "unknown"),
                        level=item.get("level", "B"),
                        url=item.get("url", ""),
                    )
                )
            except Exception:
                continue
    return EventDetail(
        id=ev.id,
        module=ev.module.value,
        title=(content.title if content else None),
        card_summary=ev.card_summary,
        detail_summary=ev.detail_summary,
        tags=list(ev.tags or []),
        importance=ev.importance,
        hotness=ev.hotness,
        source_count=ev.source_count,
        sources=sources_payload,
        first_seen=ev.first_seen,
        last_update=ev.last_update,
    )


@router.get("/daily-brief", response_model=list[EventCard])
def daily_brief(
    date_str: Optional[str] = Query(default=None, alias="date", description="YYYY-MM-DD，默认今天"),
    limit: int = Query(default=20, ge=1, le=100),
    module: Optional[str] = Query(default=None, description="可选按模块过滤"),
) -> list[EventCard]:
    """当日简报：按 importance 倒序返回当日有更新的事件卡片。"""
    target_date = _parse_date(date_str)
    day_start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max, tzinfo=timezone.utc)

    stmt = (
        select(Event)
        .where(Event.status.in_(_VISIBLE_STATUSES))
        .where(Event.last_update >= day_start)
        .where(Event.last_update <= day_end)
        .order_by(desc(Event.importance), desc(Event.last_update))
        .limit(limit)
    )
    if module:
        stmt = stmt.where(Event.module == _parse_module(module))

    with get_session() as s:
        events = s.execute(stmt).scalars().all()
        return [_to_card(ev) for ev in events]


@router.get("/event/{event_id}", response_model=EventDetail)
def event_detail(event_id: int) -> EventDetail:
    """事件详情：含 detail_summary、信源列表。"""
    with get_session() as s:
        ev = s.get(Event, event_id)
        if ev is None or ev.status not in _VISIBLE_STATUSES:
            raise HTTPException(status_code=404, detail="event not found")
        return _to_detail(ev)


@router.get("/feed", response_model=FeedPage)
def feed(
    cursor: Optional[str] = Query(default=None, description="上一页返回的 next_cursor"),
    limit: int = Query(default=20, ge=1, le=50),
    module: Optional[str] = Query(default=None),
) -> FeedPage:
    """信息流分页：按 last_update 倒序，使用 (last_update, id) 复合 cursor。"""
    stmt = (
        select(Event)
        .where(Event.status.in_(_VISIBLE_STATUSES))
        .order_by(desc(Event.last_update), desc(Event.id))
        .limit(limit + 1)
    )
    if module:
        stmt = stmt.where(Event.module == _parse_module(module))

    if cursor:
        last_update_iso, last_id = _decode_cursor(cursor)
        stmt = stmt.where(
            (Event.last_update < last_update_iso)
            | ((Event.last_update == last_update_iso) & (Event.id < last_id))
        )

    with get_session() as s:
        rows = s.execute(stmt).scalars().all()
        has_more = len(rows) > limit
        page = rows[:limit]
        items = [_to_card(ev) for ev in page]
        next_cursor = (
            _encode_cursor(page[-1].last_update, page[-1].id) if (has_more and page) else None
        )
        return FeedPage(items=items, next_cursor=next_cursor)


@router.get("/ranking", response_model=list[EventCard])
def ranking_top(
    module: Optional[str] = Query(default=None, description="可选按模块过滤"),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[EventCard]:
    """热度榜单 TOP-N：优先读 Redis ZSet（O(logN)），Redis 不可用回退 DB importance 排序。"""
    module_val = _parse_module(module).value if module else None
    ids = ranking.top(module_val, limit)
    with get_session() as s:
        if ids:
            events = s.execute(select(Event).where(Event.id.in_(ids))).scalars().all()
            by_id = {ev.id: ev for ev in events}
            ordered = [
                by_id[i]
                for i in ids
                if i in by_id and by_id[i].status in _VISIBLE_STATUSES
            ]
            return [_to_card(ev) for ev in ordered]
        stmt = (
            select(Event)
            .where(Event.status.in_(_VISIBLE_STATUSES))
            .order_by(desc(Event.importance), desc(Event.last_update))
            .limit(limit)
        )
        if module:
            stmt = stmt.where(Event.module == _parse_module(module))
        events = s.execute(stmt).scalars().all()
        return [_to_card(ev) for ev in events]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _parse_date(date_str: Optional[str]) -> date:
    if not date_str:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(date_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid date: {date_str}") from exc


def _parse_module(value: str) -> Module:
    try:
        return Module(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid module: {value}") from exc


def _encode_cursor(last_update: datetime, last_id: int) -> str:
    payload = json.dumps({"t": last_update.isoformat(), "id": last_id}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        return datetime.fromisoformat(data["t"]), int(data["id"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc
