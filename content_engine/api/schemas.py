"""HTTP 响应 schema（pydantic v2）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class EventCard(BaseModel):
    """iOS 卡片流单卡。"""

    id: int
    module: str
    title: str | None = None
    card_summary: str | None = None
    importance: float = 0.0
    hotness: float = 0.5
    source_count: int = 1
    tags: list[str] = Field(default_factory=list)
    last_update: datetime


class EventSourceItem(BaseModel):
    name: str
    level: str
    url: str


class EventDetail(BaseModel):
    """事件详情页。"""

    id: int
    module: str
    title: str | None = None
    card_summary: str | None = None
    detail_summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.0
    hotness: float = 0.5
    source_count: int = 1
    sources: list[EventSourceItem] = Field(default_factory=list)
    first_seen: datetime
    last_update: datetime


class FeedPage(BaseModel):
    """信息流分页响应。"""

    items: list[EventCard]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# 阶段 4.2：CMS 质检后台 schema
# ---------------------------------------------------------------------------
class ReviewItem(BaseModel):
    """待审事件（含护栏拦截原因、复核打标）。"""

    id: int
    module: str
    status: str
    title: str | None = None
    card_summary: str | None = None
    detail_summary: str | None = None
    importance: float = 0.0
    source_count: int = 1
    needs_split: bool | None = None
    suggested_merge_id: int | None = None
    guard_violations: list[str] = Field(default_factory=list)
    last_update: datetime


class ReviewActionRequest(BaseModel):
    """质检动作请求体。"""

    reviewer: str = Field(min_length=1, max_length=64)
    note: str | None = None
    # edit 用：覆盖卡片/详情摘要
    card_summary: str | None = None
    detail_summary: str | None = None
    # merge 用：把当前事件并入的目标事件 id
    target_event_id: int | None = None


class ReviewActionResponse(BaseModel):
    """质检动作结果。"""

    event_id: int
    action: str
    status: str
    log_id: int
