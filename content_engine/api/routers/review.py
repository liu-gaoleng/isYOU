"""阶段 4.2：CMS 质检后台接口。

供 CMS（React 后台）调用，对进入 ``reviewing`` 的事件做人工质检闭环。
所有动作写 ``review_logs``（reviewer / action / before / after / note），保证可回溯。

鉴权：静态 Token 头校验（``X-Admin-Token`` == ``settings.admin.token``）。
token 未配置（空）时所有请求 401，避免误开放（铁律：脏内容零直发）。
正式 RBAC 留到 CMS 正式开发阶段替换。

端点（全部挂在 /api/v1/admin 前缀下）：
- GET  /review/queue                  待审队列（reviewing + 复核打标）
- POST /review/{event_id}/approve     通过 → published
- POST /review/{event_id}/reject      驳回 → rejected
- POST /review/{event_id}/edit        编辑摘要（不改状态）
- POST /review/{event_id}/merge       合并到 target_event_id（当前事件 rejected）
- POST /review/{event_id}/split       标记需拆分（needs_split=True，人工后续处理）
- POST /review/{event_id}/pin         置顶（importance 提到 100）
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import desc, or_, select

from content_engine.config import settings
from content_engine.models import (
    Event,
    EventContent,
    EventStatus,
    Module,
    ReviewLog,
    get_session,
)

from ..schemas import ReviewActionRequest, ReviewActionResponse, ReviewItem

router = APIRouter(prefix="/admin", tags=["admin-review"])


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """静态 Token 头校验：token 未配置或不匹配一律 401。"""
    expected = settings.admin.token
    if not expected or x_admin_token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing admin token")


def _latest_content(ev: Event) -> EventContent | None:
    return max(ev.contents, key=lambda c: c.version) if ev.contents else None


def _guard_violations(ev: Event) -> list[str]:
    content = _latest_content(ev)
    if content and content.llm_meta:
        guard = content.llm_meta.get("guard") or {}
        return list(guard.get("violations") or [])
    return []


def _to_item(ev: Event) -> ReviewItem:
    content = _latest_content(ev)
    return ReviewItem(
        id=ev.id,
        module=ev.module.value,
        status=ev.status.value,
        title=(content.title if content else None),
        card_summary=ev.card_summary,
        detail_summary=ev.detail_summary,
        importance=ev.importance,
        source_count=ev.source_count,
        needs_split=ev.needs_split,
        suggested_merge_id=ev.suggested_merge_id,
        guard_violations=_guard_violations(ev),
        last_update=ev.last_update,
    )


def _write_log(
    s,
    *,
    event_id: int,
    reviewer: str,
    action: str,
    before: dict | None,
    after: dict | None,
    note: str | None,
) -> ReviewLog:
    log = ReviewLog(
        event_id=event_id,
        reviewer=reviewer,
        action=action,
        before=before,
        after=after,
        note=note,
    )
    s.add(log)
    s.flush()
    return log


@router.get("/review/queue", response_model=list[ReviewItem], dependencies=[Depends(require_admin)])
def review_queue(
    module: Optional[str] = Query(default=None, description="可选按模块过滤"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ReviewItem]:
    """待审队列：reviewing 状态 + HDBSCAN 复核打标（needs_split / suggested_merge_id）。"""
    stmt = (
        select(Event)
        .where(
            or_(
                Event.status == EventStatus.reviewing,
                Event.needs_split.is_(True),
                Event.suggested_merge_id.is_not(None),
            )
        )
        .order_by(desc(Event.importance), desc(Event.last_update))
        .limit(limit)
    )
    if module:
        stmt = stmt.where(Event.module == _parse_module(module))
    with get_session() as s:
        events = s.execute(stmt).scalars().all()
        return [_to_item(ev) for ev in events]


@router.post(
    "/review/{event_id}/approve",
    response_model=ReviewActionResponse,
    dependencies=[Depends(require_admin)],
)
def approve(event_id: int, body: ReviewActionRequest) -> ReviewActionResponse:
    """通过质检 → published。"""
    return _transition(event_id, body, "approve", EventStatus.published)


@router.post(
    "/review/{event_id}/reject",
    response_model=ReviewActionResponse,
    dependencies=[Depends(require_admin)],
)
def reject(event_id: int, body: ReviewActionRequest) -> ReviewActionResponse:
    """驳回 → rejected。"""
    return _transition(event_id, body, "reject", EventStatus.rejected)


@router.post(
    "/review/{event_id}/edit",
    response_model=ReviewActionResponse,
    dependencies=[Depends(require_admin)],
)
def edit(event_id: int, body: ReviewActionRequest) -> ReviewActionResponse:
    """编辑卡片/详情摘要（不改状态）。"""
    with get_session() as s:
        ev = _get_event(s, event_id)
        before = {"card_summary": ev.card_summary, "detail_summary": ev.detail_summary}
        if body.card_summary is not None:
            ev.card_summary = body.card_summary
        if body.detail_summary is not None:
            ev.detail_summary = body.detail_summary
        after = {"card_summary": ev.card_summary, "detail_summary": ev.detail_summary}
        log = _write_log(
            s,
            event_id=event_id,
            reviewer=body.reviewer,
            action="edit",
            before=before,
            after=after,
            note=body.note,
        )
        return ReviewActionResponse(
            event_id=event_id, action="edit", status=ev.status.value, log_id=log.id
        )


@router.post(
    "/review/{event_id}/merge",
    response_model=ReviewActionResponse,
    dependencies=[Depends(require_admin)],
)
def merge(event_id: int, body: ReviewActionRequest) -> ReviewActionResponse:
    """合并：把当前事件并入 target_event_id（当前事件标 rejected）。

    保守实现：仅改当前事件状态 + 留痕，不物理迁移 event_articles，
    避免破坏可回溯性（与 recluster「只打标不改归属」一致）。
    """
    if body.target_event_id is None:
        raise HTTPException(status_code=400, detail="merge 需要 target_event_id")
    with get_session() as s:
        ev = _get_event(s, event_id)
        target = s.get(Event, body.target_event_id)
        if target is None:
            raise HTTPException(status_code=404, detail="target event not found")
        before = {"status": ev.status.value}
        ev.status = EventStatus.rejected
        log = _write_log(
            s,
            event_id=event_id,
            reviewer=body.reviewer,
            action="merge",
            before=before,
            after={"status": ev.status.value, "merged_into": body.target_event_id},
            note=body.note,
        )
        return ReviewActionResponse(
            event_id=event_id, action="merge", status=ev.status.value, log_id=log.id
        )


@router.post(
    "/review/{event_id}/split",
    response_model=ReviewActionResponse,
    dependencies=[Depends(require_admin)],
)
def split(event_id: int, body: ReviewActionRequest) -> ReviewActionResponse:
    """标记需拆分（needs_split=True），实际拆分由后续工具处理。"""
    with get_session() as s:
        ev = _get_event(s, event_id)
        before = {"needs_split": ev.needs_split}
        ev.needs_split = True
        log = _write_log(
            s,
            event_id=event_id,
            reviewer=body.reviewer,
            action="split",
            before=before,
            after={"needs_split": True},
            note=body.note,
        )
        return ReviewActionResponse(
            event_id=event_id, action="split", status=ev.status.value, log_id=log.id
        )


@router.post(
    "/review/{event_id}/pin",
    response_model=ReviewActionResponse,
    dependencies=[Depends(require_admin)],
)
def pin(event_id: int, body: ReviewActionRequest) -> ReviewActionResponse:
    """置顶：把 importance 提到 100（榜单/简报排序最前）。"""
    with get_session() as s:
        ev = _get_event(s, event_id)
        before = {"importance": ev.importance}
        ev.importance = 100.0
        log = _write_log(
            s,
            event_id=event_id,
            reviewer=body.reviewer,
            action="pin",
            before=before,
            after={"importance": 100.0},
            note=body.note,
        )
        return ReviewActionResponse(
            event_id=event_id, action="pin", status=ev.status.value, log_id=log.id
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _transition(
    event_id: int, body: ReviewActionRequest, action: str, new_status: EventStatus
) -> ReviewActionResponse:
    with get_session() as s:
        ev = _get_event(s, event_id)
        before = {"status": ev.status.value}
        ev.status = new_status
        log = _write_log(
            s,
            event_id=event_id,
            reviewer=body.reviewer,
            action=action,
            before=before,
            after={"status": new_status.value},
            note=body.note,
        )
        return ReviewActionResponse(
            event_id=event_id, action=action, status=new_status.value, log_id=log.id
        )


def _get_event(s, event_id: int) -> Event:
    ev = s.get(Event, event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="event not found")
    return ev


def _parse_module(value: str) -> Module:
    try:
        return Module(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid module: {value}") from exc
