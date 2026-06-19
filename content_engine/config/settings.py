"""统一配置加载层（pydantic-settings）。

来源优先级：进程环境变量 > .env 文件 > 默认值。

外部使用：
    from content_engine.config import settings
    print(settings.database_url)
    print(settings.llm.model)

设计要点：
1. 一个 Settings 实例做单例；
2. 嵌套分组（llm / threshold）便于阅读，但仍由扁平的环境变量驱动；
3. 与 .env.example 中的变量名一一对应；
4. 启动期校验：如 RD_LLM_API_KEY 留空也能跑（摘要走抽取式兜底），但 DATABASE_URL 必填。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录的 .env（content_engine 在子目录里，需向上一级）
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class LLMSettings(BaseSettings):
    """LLM 相关配置（兼容 OpenAI 格式）。

    与 pipeline_demo 沿用 RD_LLM_* 前缀；为空则摘要阶段走抽取式兜底。
    """

    api_key: str = Field(default="", validation_alias="RD_LLM_API_KEY")
    base_url: str = Field(
        default="https://api.openai.com/v1", validation_alias="RD_LLM_BASE_URL"
    )
    model: str = Field(default="gpt-4o-mini", validation_alias="RD_LLM_MODEL")

    # 阶段 3.1：调用韧性（令牌桶限流 + 重试 + 指数退避）
    rate_per_sec: float = Field(default=3.0, validation_alias="RD_LLM_RATE_PER_SEC")
    max_retries: int = Field(default=3, validation_alias="RD_LLM_MAX_RETRIES")
    backoff_base: float = Field(default=1.0, validation_alias="RD_LLM_BACKOFF_BASE")
    timeout: int = Field(default=60, validation_alias="RD_LLM_TIMEOUT")

    # 并发化：summarize 阶段线程池并发度。推理模型单次 ~30s，串行 167 条 ≈ 80 分钟；
    # 并发后整体提速 ≈ 该倍数。令牌桶 rate_per_sec 仍是全局限流护栏（高并发不会超 QPS）。
    summarize_concurrency: int = Field(
        default=8, validation_alias="RD_LLM_SUMMARIZE_CONCURRENCY"
    )

    # 阶段 D：成本核算单价（美元/千 token）。默认 0 表示不计费（仅累计 token）；
    # 配置后由 llm_client 按 usage 自动换算 cost，写入 llm_meta 供成本看板聚合。
    cost_per_1k_prompt: float = Field(
        default=0.0, validation_alias="RD_LLM_COST_PER_1K_PROMPT"
    )
    cost_per_1k_completion: float = Field(
        default=0.0, validation_alias="RD_LLM_COST_PER_1K_COMPLETION"
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class ThresholdSettings(BaseSettings):
    """管线阈值（与 pipeline_demo/config.py 对齐，可被环境变量覆盖）。"""

    dedup_threshold: float = Field(default=0.92, validation_alias="RD_DEDUP_THRESHOLD")
    cluster_threshold: float = Field(default=0.86, validation_alias="RD_CLUSTER_THRESHOLD")
    max_per_source: int = Field(default=12, validation_alias="RD_MAX_PER_SOURCE")
    # 事件聚类时间窗（小时），方案 §5.2
    cluster_window_hours: int = Field(default=72, validation_alias="RD_CLUSTER_WINDOW_HOURS")
    # 时效衰减常数（小时），方案 §7.1
    recency_tau_hours: float = Field(default=12.0, validation_alias="RD_RECENCY_TAU_HOURS")
    # SimHash 近似去重：汉明距离 ≤ 该阈值则认定为重复（64-bit 上 ≤3 是业界常用线）
    simhash_hamming_threshold: int = Field(
        default=3, validation_alias="RD_SIMHASH_HAMMING_THRESHOLD"
    )
    # SimHash 仅在同一时间窗内查找候选（小时），避免全表扫描
    simhash_window_hours: int = Field(
        default=72, validation_alias="RD_SIMHASH_WINDOW_HOURS"
    )
    # 信源健康：连续失败 N 次触发 logging.WARNING
    source_failure_alert_threshold: int = Field(
        default=3, validation_alias="RD_SOURCE_FAILURE_ALERT_THRESHOLD"
    )
    # 阶段 2.1：分类置信度低于此阈值时调用 LLM 兜底
    cls_llm_threshold: float = Field(
        default=0.6, validation_alias="RD_CLS_LLM_THRESHOLD"
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class EmbeddingSettings(BaseSettings):
    """Embedding 配置（阶段 1.3）。

    默认 provider=local，模型 BAAI/bge-small-zh-v1.5（512 维）；
    后续切远程 OpenAI 兼容 API 时改 provider=remote 即可。
    """

    provider: str = Field(default="local", validation_alias="RD_EMBEDDING_PROVIDER")
    model_name: str = Field(
        default="BAAI/bge-small-zh-v1.5", validation_alias="RD_EMBEDDING_MODEL"
    )
    dim: int = Field(default=512, validation_alias="RD_EMBEDDING_DIM")
    device: str = Field(default="cpu", validation_alias="RD_EMBEDDING_DEVICE")
    batch_size: int = Field(default=32, validation_alias="RD_EMBEDDING_BATCH_SIZE")
    # 语义去重阈值（cos 相似度），方案 §5.1
    semantic_dedup_threshold: float = Field(
        default=0.92, validation_alias="RD_SEMANTIC_DEDUP_THRESHOLD"
    )
    # 语义去重时间窗（小时）
    semantic_dedup_window_hours: int = Field(
        default=72, validation_alias="RD_SEMANTIC_DEDUP_WINDOW_HOURS"
    )

    # ---- 远程 provider 配置（provider=remote 时启用，OpenAI 兼容 /v1/embeddings）----
    remote_base_url: str = Field(
        default="", validation_alias="RD_EMBEDDING_REMOTE_BASE_URL"
    )
    remote_api_key: str = Field(
        default="", validation_alias="RD_EMBEDDING_REMOTE_API_KEY"
    )
    remote_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="RD_EMBEDDING_REMOTE_MODEL",
    )
    remote_timeout: int = Field(
        default=30, validation_alias="RD_EMBEDDING_REMOTE_TIMEOUT"
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class RankingSettings(BaseSettings):
    """阶段 3.4：Redis 榜单配置。"""

    enabled: bool = Field(default=True, validation_alias="RD_RANK_ENABLED")
    # 每个 ZSet 最多保留多少条（按 importance 裁剪）
    keep_top: int = Field(default=500, validation_alias="RD_RANK_KEEP")

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class GuardSettings(BaseSettings):
    """阶段 4.1：发布前防幻觉护栏配置。

    机器卡点不通过的事件不直发，统一打回 ``EventStatus.reviewing`` 进人工。
    """

    # 总开关：关闭则 publish 阶段直接放行（仅本地调试用）
    enabled: bool = Field(default=True, validation_alias="RD_GUARD_ENABLED")
    # 详情摘要里出现、但任一信源原文都查无的数字 token 超过该比例 → 拦截
    max_unverified_number_ratio: float = Field(
        default=0.5, validation_alias="RD_GUARD_MAX_UNVERIFIED_NUMBER_RATIO"
    )
    # card/detail 摘要最短中文字符数（过短视为生成异常）
    min_summary_chars: int = Field(default=10, validation_alias="RD_GUARD_MIN_SUMMARY_CHARS")
    # 敏感词命中即拦截（逗号分隔，可被环境变量覆盖）；默认取自内置基础词表
    sensitive_words: str = Field(default="", validation_alias="RD_GUARD_SENSITIVE_WORDS")

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AdminSettings(BaseSettings):
    """阶段 4.2：CMS 质检后台接口鉴权（静态 Token 头校验）。"""

    # 为空则质检接口拒绝所有请求（避免误开放）
    token: str = Field(default="", validation_alias="RD_ADMIN_TOKEN")

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AuthSettings(BaseSettings):
    """阶段 3.1：C 端账号鉴权（Sign in with Apple + 本地 JWT）。

    - Sign in with Apple：客户端拿到 identityToken（Apple 签发的 JWT）后传给后端，
      后端用 Apple 公钥（JWKS）验签，校验 iss/aud/exp，取 sub 作为 apple_user_id；
    - 本地 JWT：验签通过后由后端签发 HS256 access token，后续接口用它鉴权。
    铁律：密钥来自环境变量，绝不硬编码；dev_login 默认关闭，仅本地联调临时开启。
    """

    # 本地 JWT 签名密钥（HS256）。为空则签发/校验直接拒绝（避免空密钥裸奔）。
    jwt_secret: str = Field(default="", validation_alias="RD_AUTH_JWT_SECRET")
    # access token 有效期（分钟）
    jwt_expire_minutes: int = Field(
        default=43200, validation_alias="RD_AUTH_JWT_EXPIRE_MINUTES"
    )
    # 本地 JWT 的 issuer 标识
    jwt_issuer: str = Field(default="redu", validation_alias="RD_AUTH_JWT_ISSUER")

    # Sign in with Apple 校验参数
    apple_bundle_id: str = Field(default="", validation_alias="RD_AUTH_APPLE_BUNDLE_ID")
    apple_issuer: str = Field(
        default="https://appleid.apple.com", validation_alias="RD_AUTH_APPLE_ISSUER"
    )
    apple_jwks_url: str = Field(
        default="https://appleid.apple.com/auth/keys",
        validation_alias="RD_AUTH_APPLE_JWKS_URL",
    )
    # Apple JWKS 本地缓存秒数（公钥很少轮换，缓存避免每次登录都拉取）
    apple_jwks_cache_ttl: int = Field(
        default=86400, validation_alias="RD_AUTH_APPLE_JWKS_CACHE_TTL"
    )

    # dev 测试登录通道开关：开启后 /auth/dev-login 可不经 Apple 直接签发 JWT（仅本地联调）
    dev_login_enabled: bool = Field(
        default=False, validation_alias="RD_AUTH_DEV_LOGIN_ENABLED"
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class BillingSettings(BaseSettings):
    """阶段 3.2：会员订阅 / Apple IAP（StoreKit 2）收据校验配置。

    校验链路：客户端上送 StoreKit 2 的 JWS 交易（Apple 用其私钥 ES256 签名，
    头部 x5c 携带证书链）；服务端用内置 Apple Root CA 校验证书链，再用叶子证书
    公钥验签 JWS，取 payload 的 transaction/expires/product 等字段。

    可选：App Store Server API（查询订阅状态 / 退款）需要 issuer_id + key_id + 私钥(.p8)，
    用于服务端主动查权威订阅态、对账退款；本阶段以 JWS 离线验签为主，Server API 为增强项。

    铁律：商品 id 必须与 App Store Connect 一致；Apple Root CA 证书路径来自配置，
    缺失时收据校验直接拒绝（不裸放行），避免伪造交易绕过付费墙。
    """

    # 自动续订订阅的 App Store Connect product_id（与 SubscriptionPlan 一一对应）
    product_monthly: str = Field(
        default="com.redu.app.member.monthly",
        validation_alias="RD_BILLING_PRODUCT_MONTHLY",
    )
    product_quarterly: str = Field(
        default="com.redu.app.member.quarterly",
        validation_alias="RD_BILLING_PRODUCT_QUARTERLY",
    )
    product_yearly: str = Field(
        default="com.redu.app.member.yearly",
        validation_alias="RD_BILLING_PRODUCT_YEARLY",
    )

    # Apple Root CA (G3) PEM 文件路径，用于校验 JWS x5c 证书链；为空则拒绝校验
    apple_root_ca_path: str = Field(
        default="", validation_alias="RD_BILLING_APPLE_ROOT_CA_PATH"
    )
    # App 的 bundle id（校验 JWS payload 的 bundleId 一致）；为空则不强校验
    bundle_id: str = Field(default="", validation_alias="RD_BILLING_BUNDLE_ID")
    # 接受的环境（Production / Sandbox），逗号分隔；联调期含 Sandbox
    accepted_environments: str = Field(
        default="Production,Sandbox", validation_alias="RD_BILLING_ACCEPTED_ENVIRONMENTS"
    )

    # ---- App Store Server API 凭据（可选增强：服务端主动查订阅/退款）----
    server_api_issuer_id: str = Field(
        default="", validation_alias="RD_BILLING_SERVER_API_ISSUER_ID"
    )
    server_api_key_id: str = Field(
        default="", validation_alias="RD_BILLING_SERVER_API_KEY_ID"
    )
    # App Store Connect 下载的 .p8 私钥文件路径（EC P-256）
    server_api_private_key_path: str = Field(
        default="", validation_alias="RD_BILLING_SERVER_API_PRIVATE_KEY_PATH"
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class CelerySettings(BaseSettings):
    """阶段 4.3：Celery 调度配置（broker / backend 默认复用 Redis 不同 db）。"""

    broker_url: str = Field(
        default="redis://localhost:6379/1", validation_alias="CELERY_BROKER_URL"
    )
    result_backend: str = Field(
        default="redis://localhost:6379/2", validation_alias="CELERY_RESULT_BACKEND"
    )

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


class Settings(BaseSettings):
    """全局配置入口。"""

    # DB（SQLAlchemy 直接读这一条）
    database_url: str = Field(
        default="postgresql+psycopg://rd:rd@localhost:5432/redu",
        validation_alias="DATABASE_URL",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0", validation_alias="REDIS_URL"
    )

    # 嵌套分组
    llm: LLMSettings = Field(default_factory=LLMSettings)
    threshold: ThresholdSettings = Field(default_factory=ThresholdSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    ranking: RankingSettings = Field(default_factory=RankingSettings)
    guard: GuardSettings = Field(default_factory=GuardSettings)
    admin: AdminSettings = Field(default_factory=AdminSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    billing: BillingSettings = Field(default_factory=BillingSettings)
    celery: CelerySettings = Field(default_factory=CelerySettings)

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例工厂：避免重复读 .env / 实例化。"""
    return Settings()


# 模块级便捷别名：from content_engine.config import settings
settings = get_settings()


__all__ = ["Settings", "LLMSettings", "ThresholdSettings", "EmbeddingSettings", "RankingSettings", "GuardSettings", "AdminSettings", "AuthSettings", "BillingSettings", "CelerySettings", "settings", "get_settings"]
