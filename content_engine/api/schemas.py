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


class DeepContent(BaseModel):
    """付费深度解读（服务端按会员态裁剪）。

    - 会员：``is_locked=False`` + 完整 ``content``；
    - 非会员/未登录：``is_locked=True`` + 截断 ``preview`` + ``paywall`` 引导。
    正文 content 仅在解锁时下发，非会员永远拿不到全文（防客户端绕过）。
    """

    is_locked: bool
    content: str | None = None
    preview: str | None = None
    paywall: dict | None = None


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
    deep_content: DeepContent | None = None
    first_seen: datetime
    last_update: datetime


class FeedPage(BaseModel):
    """信息流分页响应。"""

    items: list[EventCard]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# 阶段 3.1：账号鉴权 schema（Sign in with Apple + 本地 JWT）
# ---------------------------------------------------------------------------
class AppleLoginRequest(BaseModel):
    """Sign in with Apple 登录请求：客户端上送 Apple identityToken。"""

    identity_token: str = Field(min_length=1, description="Apple 签发的 identityToken(JWT)")
    # 首次登录时 Apple 可能携带昵称，仅首次返回，后端首登时落库
    display_name: str | None = Field(default=None, max_length=64)


class DevLoginRequest(BaseModel):
    """dev 测试登录（仅本地联调，受 RD_AUTH_DEV_LOGIN_ENABLED 开关保护）。"""

    apple_user_id: str = Field(min_length=1, max_length=64, description="模拟的 Apple sub")
    email: str | None = Field(default=None, max_length=256)
    display_name: str | None = Field(default=None, max_length=64)
    # 可选：直接置为会员，便于联调付费墙解锁态
    as_member: bool = False


class UserProfile(BaseModel):
    """当前登录用户信息。"""

    id: int
    email: str | None = None
    display_name: str | None = None
    created_via: str = "apple"
    member_tier: str = "free"
    is_member: bool = False
    member_expire_at: datetime | None = None


class LoginResponse(BaseModel):
    """登录成功响应：本地 access token + 用户信息。"""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserProfile


# ---------------------------------------------------------------------------
# 阶段 3.4：收藏 / 阅读历史 / 推送设置（按登录用户 user_id）
# ---------------------------------------------------------------------------
class FavoriteState(BaseModel):
    """收藏状态切换结果。"""

    event_id: int
    is_favorited: bool


class FavoriteCard(EventCard):
    """收藏列表项：事件卡片 + 收藏时间。"""

    favorited_at: datetime


class HistoryCard(EventCard):
    """阅读历史项：事件卡片 + 浏览时间。"""

    viewed_at: datetime


class HistoryClearResult(BaseModel):
    cleared: bool


class PushSettings(BaseModel):
    """推送设置（读 / 写共用）。"""

    daily_push: bool = True
    push_time: str = "08:00"
    breaking_push: bool = False


class PushSettingsUpdate(BaseModel):
    """推送设置更新（全部可选，仅更新传入字段）。"""

    daily_push: bool | None = None
    push_time: str | None = None
    breaking_push: bool | None = None


# ---------------------------------------------------------------------------
# 阶段 4.2：设备 token 注册（APNs 推送链路起点）
# ---------------------------------------------------------------------------
class DeviceRegisterRequest(BaseModel):
    """设备 token 注册请求。

    - ``token``：APNs deviceToken 的 hex 字符串（64 字符），iOS 端在
      ``didRegisterForRemoteNotificationsWithDeviceToken`` 中将 Data 转 hex；
    - ``bundle_id``：可选，自报 bundle 便于后端日志归因；
    - ``environment``：``sandbox`` / ``production``，决定下发的 APNs 主机。
    """

    token: str = Field(min_length=16, max_length=255, description="APNs hex device token")
    bundle_id: str | None = Field(default=None, max_length=128)
    environment: str = Field(default="production", pattern="^(production|sandbox)$")


class DeviceTokenInfo(BaseModel):
    """设备 token 注册结果（返回给客户端用于自检）。"""

    token: str
    environment: str
    bundle_id: str | None = None
    is_active: bool = True
    last_seen_at: datetime | None = None


# ---------------------------------------------------------------------------
# 阶段 3.2：会员订阅 / Apple IAP 收据校验 schema
# ---------------------------------------------------------------------------
class PlanItem(BaseModel):
    """订阅档位（供客户端展示与 StoreKit 商品匹配）。"""

    plan: str  # monthly / quarterly / yearly
    product_id: str
    period_days: int


class MembershipStatus(BaseModel):
    """当前会员态（付费墙判定的对外视图）。"""

    is_member: bool
    member_tier: str = "free"
    member_expire_at: datetime | None = None
    plan: str | None = None
    auto_renew: bool = False
    subscription_status: str | None = None  # active / expired / refunded


class VerifyReceiptRequest(BaseModel):
    """客户端上送 StoreKit 2 已签名交易（JWSTransaction）。"""

    signed_transaction: str = Field(min_length=1, description="StoreKit2 JWSTransaction")


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
