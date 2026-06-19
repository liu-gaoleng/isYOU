"""阶段 3.4：C 端收藏 / 阅读历史 / 推送设置（生产 API，按登录用户 user_id）。

端点（挂在 /api/v1 前缀下，全部需登录）：
- POST   /me/favorites/{event_id}   收藏事件（幂等）
- DELETE /me/favorites/{event_id}   取消收藏（幂等）
- GET    /me/favorites              收藏列表（最近收藏在前）
- GET    /me/history                阅读历史（最近浏览在前）
- POST   /me/history/{event_id}     记录一次浏览（去重置顶）
- DELETE /me/history                清空阅读历史
- GET    /me/settings               读推送设置（无则返回默认）
- PUT    /me/settings               更新推送设置（仅改传入字段）

收藏 / 历史按真实 user_id 隔离（与 mock_server 的 token 维度并存，互不干扰）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, desc, select

from content_engine.models import (
    Event,
    EventStatus,
    Favorite,
    PushSetting,
    ReadingHistory,
    User,
    get_session,
)

from ..deps import get_current_user
from ..schemas import (
    FavoriteCard,
    FavoriteState,
    HistoryCard,
    HistoryClearResult,
    PushSettings,
    PushSettingsUpdate,
)
from .brief import _to_card

router = APIRouter(prefix="/me", tags=["me"])

# 与 brief 一致：仅已生成可读内容的事件可被收藏/展示
_VISIBLE_STATUSES = (
    EventStatus.summarized,
    EventStatus.scored,
    EventStatus.published,
)

# 阅读历史最多保留条数（与 mock 对齐）
_HISTORY_LIMIT = 50


def _visible_event(session, event_id: int) -> Event | None:
    """取可见事件；不存在或不可见返回 None。"""
    ev = session.get(Event, event_id)
    if ev is None or ev.status not in _VISIBLE_STATUSES:
        return None
    return ev


def _ensure_visible_event(session, event_id: int) -> Event:
    ev = _visible_event(session, event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="event not found")
    return ev


# ---------------------------------------------------------------------------
# 收藏
# ---------------------------------------------------------------------------
@router.post("/favorites/{event_id}", response_model=FavoriteState)
def add_favorite(
    event_id: int, user: User = Depends(get_current_user)
) -> FavoriteState:
    """收藏事件（幂等：已收藏再调返回 is_favorited=True）。"""
    with get_session() as s:
        _ensure_visible_event(s, event_id)
        exists = s.execute(
            select(Favorite).where(
                Favorite.user_id == user.id, Favorite.event_id == event_id
            )
        ).scalar_one_or_none()
        if exists is None:
            s.add(Favorite(user_id=user.id, event_id=event_id))
    return FavoriteState(event_id=event_id, is_favorited=True)


@router.delete("/favorites/{event_id}", response_model=FavoriteState)
def remove_favorite(
    event_id: int, user: User = Depends(get_current_user)
) -> FavoriteState:
    """取消收藏（幂等：未收藏再调也返回 is_favorited=False）。"""
    with get_session() as s:
        s.execute(
            delete(Favorite).where(
                Favorite.user_id == user.id, Favorite.event_id == event_id
            )
        )
    return FavoriteState(event_id=event_id, is_favorited=False)


@router.get("/favorites", response_model=list[FavoriteCard])
def list_favorites(user: User = Depends(get_current_user)) -> list[FavoriteCard]:
    """收藏列表：最近收藏在前；事件不可见则跳过。"""
    with get_session() as s:
        rows = (
            s.execute(
                select(Favorite)
                .where(Favorite.user_id == user.id)
                .order_by(desc(Favorite.id))
            )
            .scalars()
            .all()
        )
        out: list[FavoriteCard] = []
        for fav in rows:
            ev = _visible_event(s, fav.event_id)
            if ev is None:
                continue
            card = _to_card(ev)
            out.append(
                FavoriteCard(**card.model_dump(), favorited_at=fav.created_at)
            )
        return out


# ---------------------------------------------------------------------------
# 阅读历史
# ---------------------------------------------------------------------------
@router.post("/history/{event_id}", status_code=204)
def record_history(event_id: int, user: User = Depends(get_current_user)) -> None:
    """记录一次浏览：同 user+event 去重后更新时间，超出上限裁剪最旧。"""
    now = datetime.now(timezone.utc)
    with get_session() as s:
        _ensure_visible_event(s, event_id)
        row = s.execute(
            select(ReadingHistory).where(
                ReadingHistory.user_id == user.id,
                ReadingHistory.event_id == event_id,
            )
        ).scalar_one_or_none()
        if row is None:
            s.add(
                ReadingHistory(user_id=user.id, event_id=event_id, viewed_at=now)
            )
        else:
            row.viewed_at = now
        s.flush()
        # 裁剪：仅保留最近 _HISTORY_LIMIT 条
        keep_ids = (
            s.execute(
                select(ReadingHistory.id)
                .where(ReadingHistory.user_id == user.id)
                .order_by(desc(ReadingHistory.viewed_at), desc(ReadingHistory.id))
                .limit(_HISTORY_LIMIT)
            )
            .scalars()
            .all()
        )
        if keep_ids:
            s.execute(
                delete(ReadingHistory).where(
                    ReadingHistory.user_id == user.id,
                    ReadingHistory.id.notin_(keep_ids),
                )
            )


@router.get("/history", response_model=list[HistoryCard])
def list_history(user: User = Depends(get_current_user)) -> list[HistoryCard]:
    """阅读历史：最近浏览在前；事件不可见则跳过。"""
    with get_session() as s:
        rows = (
            s.execute(
                select(ReadingHistory)
                .where(ReadingHistory.user_id == user.id)
                .order_by(desc(ReadingHistory.viewed_at), desc(ReadingHistory.id))
            )
            .scalars()
            .all()
        )
        out: list[HistoryCard] = []
        for h in rows:
            ev = _visible_event(s, h.event_id)
            if ev is None:
                continue
            card = _to_card(ev)
            out.append(HistoryCard(**card.model_dump(), viewed_at=h.viewed_at))
        return out


@router.delete("/history", response_model=HistoryClearResult)
def clear_history(user: User = Depends(get_current_user)) -> HistoryClearResult:
    """清空当前用户阅读历史。"""
    with get_session() as s:
        s.execute(delete(ReadingHistory).where(ReadingHistory.user_id == user.id))
    return HistoryClearResult(cleared=True)


# ---------------------------------------------------------------------------
# 推送设置
# ---------------------------------------------------------------------------
@router.get("/settings", response_model=PushSettings)
def get_settings(user: User = Depends(get_current_user)) -> PushSettings:
    """读推送设置：未设置过则返回默认值（不落库）。"""
    with get_session() as s:
        row = s.execute(
            select(PushSetting).where(PushSetting.user_id == user.id)
        ).scalar_one_or_none()
        if row is None:
            return PushSettings()
        return PushSettings(
            daily_push=row.daily_push,
            push_time=row.push_time,
            breaking_push=row.breaking_push,
        )


@router.put("/settings", response_model=PushSettings)
def update_settings(
    payload: PushSettingsUpdate, user: User = Depends(get_current_user)
) -> PushSettings:
    """更新推送设置：仅改传入字段，首次更新自动建行（upsert）。"""
    with get_session() as s:
        row = s.execute(
            select(PushSetting).where(PushSetting.user_id == user.id)
        ).scalar_one_or_none()
        if row is None:
            row = PushSetting(user_id=user.id)
            s.add(row)
        if payload.daily_push is not None:
            row.daily_push = payload.daily_push
        if payload.push_time is not None:
            row.push_time = payload.push_time
        if payload.breaking_push is not None:
            row.breaking_push = payload.breaking_push
        s.flush()
        return PushSettings(
            daily_push=row.daily_push,
            push_time=row.push_time,
            breaking_push=row.breaking_push,
        )
