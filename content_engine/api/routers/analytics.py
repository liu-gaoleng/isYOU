"""阶段 4.3：自建埋点上报端点。

端点（挂在 /api/v1 前缀下）：
- POST /analytics/events    批量入库埋点事件（无需登录，匿名也能埋）

设计取舍：
- **不强制鉴权**：``app_open`` 等漏斗起点事件必须无登录也能埋；登录后带上
  Authorization Header 服务端解出 ``user_id`` 写入即可。
- **事件名白名单**：避免客户端误传任意字符串导致表膨胀；非白名单整批 422。
- **批量大小限制**：单批 1-100 条；客户端典型 5-20 条/次。
- **入库即返回 ``accepted``**：不返回事件 id 列表，减少响应体积。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from content_engine.models import AnalyticsEvent, User, get_session

from ..deps import get_optional_user
from ..schemas import (
    ANALYTICS_EVENT_NAMES,
    AnalyticsBatchRequest,
    AnalyticsBatchResponse,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/events", response_model=AnalyticsBatchResponse)
def ingest_events(
    payload: AnalyticsBatchRequest,
    user: User | None = Depends(get_optional_user),
) -> AnalyticsBatchResponse:
    """批量入库埋点事件。

    校验顺序：
    1) 单批 1-100 条（pydantic Field 已约束）；
    2) 事件名必须在白名单内（``ANALYTICS_EVENT_NAMES``），否则整批 422；
    3) 通过校验后批量 ``add_all`` 入库，单事务提交。
    """
    # 整批名称白名单校验：任一不合规拒收整批，避免半入半丢。
    bad_names = {e.name for e in payload.events if e.name not in ANALYTICS_EVENT_NAMES}
    if bad_names:
        raise HTTPException(
            status_code=422,
            detail=f"unknown event names: {sorted(bad_names)}",
        )

    user_id = user.id if user is not None else None
    rows = [
        AnalyticsEvent(
            name=e.name,
            device_id=e.device_id,
            user_id=user_id,
            app_version=e.app_version,
            os_version=e.os_version,
            platform=e.platform,
            ts_client=e.ts_client,
            props=e.props,
        )
        for e in payload.events
    ]

    with get_session() as session:
        session.add_all(rows)
        session.commit()

    return AnalyticsBatchResponse(accepted=len(rows))
