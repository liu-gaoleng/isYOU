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


__all__ = ["Settings", "LLMSettings", "ThresholdSettings", "EmbeddingSettings", "settings", "get_settings"]
