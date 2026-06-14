"""FastAPI 应用工厂（阶段 1.5）。

仅暴露三个核心只读接口，给 iOS 客户端消费：
- GET /healthz                       —— 健康检查
- GET /api/v1/daily-brief?date=...   —— 当日简报（按 score 倒序）
- GET /api/v1/event/{id}             —— 事件详情
- GET /api/v1/feed?cursor=...        —— 信息流分页（按 last_update 倒序）
"""

from __future__ import annotations

from fastapi import FastAPI

from ..logging_config import configure_logging
from .routers import brief, metrics, review

configure_logging()

app = FastAPI(
    title="热读 Content Engine API",
    version="0.1.0",
    description="iOS-first 内容引擎只读接口（阶段 1.5）",
)


@app.get("/healthz", tags=["meta"])
def healthz() -> dict:
    """健康检查：进程存活即返回 ok，不查 DB 以免误报。"""
    return {"status": "ok"}


app.include_router(brief.router, prefix="/api/v1")
app.include_router(review.router, prefix="/api/v1")
app.include_router(metrics.router, prefix="/api/v1")
