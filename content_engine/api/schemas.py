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


# ---------------------------------------------------------------------------
# 阶段 D：可观测性报表 schema
# ---------------------------------------------------------------------------
class DailyCount(BaseModel):
    """按日聚合的计数（YYYY-MM-DD → count）。"""

    date: str
    count: int


class ConfidenceBuckets(BaseModel):
    """分类置信度分布（规则/LLM 分类结果的健康度）。"""

    high: int = 0  # >= 0.8
    mid: int = 0  # 0.5 ~ 0.8
    low: int = 0  # < 0.5
    unknown: int = 0  # 尚未分类（cls_confidence 为空）


class SourceHealthCount(BaseModel):
    """信源健康状态聚合。"""

    status_counts: dict[str, int] = Field(default_factory=dict)
    failing_sources: int = 0  # 最近一次记录连续失败 > 0 的信源数


class MetricsOverview(BaseModel):
    """可观测性总览看板（报表首页）。"""

    generated_at: datetime
    window_days: int
    # 产出量
    events_total: int = 0
    events_by_status: dict[str, int] = Field(default_factory=dict)
    events_by_module: dict[str, int] = Field(default_factory=dict)
    daily_published: list[DailyCount] = Field(default_factory=list)
    # 质检
    review_action_counts: dict[str, int] = Field(default_factory=dict)
    review_pass_rate: float = 0.0
    # 护栏
    guard_checked: int = 0
    guard_intercepted: int = 0
    guard_interception_rate: float = 0.0
    # 分类置信度
    classification_confidence: ConfidenceBuckets = Field(default_factory=ConfidenceBuckets)
    # 信源健康
    source_health: SourceHealthCount = Field(default_factory=SourceHealthCount)
    # LLM 成本 & 管线
    llm_cost_total: float = 0.0
    pipeline_runs_total: int = 0
    pipeline_success_rate: float = 0.0


class PipelineRunItem(BaseModel):
    """单次管线运行记录（运行历史报表）。"""

    id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    llm_cost: float = 0.0
    error: str | None = None
    stages: dict = Field(default_factory=dict)
