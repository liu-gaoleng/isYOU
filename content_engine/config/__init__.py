"""配置加载层（pydantic-settings）。

统一从环境变量 / .env 读取 LLM / DB / Redis / 阈值等配置。

外部使用：
    from content_engine.config import settings
"""

from .settings import EmbeddingSettings, LLMSettings, Settings, ThresholdSettings, get_settings, settings

__all__ = [
    "Settings",
    "LLMSettings",
    "ThresholdSettings",
    "EmbeddingSettings",
    "settings",
    "get_settings",
]
