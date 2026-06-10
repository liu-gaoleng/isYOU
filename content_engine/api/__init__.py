"""HTTP API 包（阶段 1.5）。

入口：`content_engine.api.app:app`
启动方式：`uvicorn content_engine.api.app:app --reload`
"""

from .app import app

__all__ = ["app"]
