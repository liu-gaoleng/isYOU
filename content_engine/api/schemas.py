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
