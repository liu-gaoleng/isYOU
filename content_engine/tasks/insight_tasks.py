"""热点透视生成任务（Celery）。

结构仿 push_tasks：业务本体是纯函数 ``generate_insight``（便于单测），
``generate_insight_task`` 是薄 Celery 壳。

并发/失败约定（详见 services/insight.py 模块 docstring）：
- 入口 ``begin_generation`` 条件更新，rowcount=0 直接返回（防 acks_late 重投烧 token）；
- LLM / 解析失败 → 落 failed，**不上抛**（不触发 celery 重试风暴，用户可重试）；
- DB 异常上抛，由 acks_late 重投 + POST 端 stale TTL 双兜底。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import settings
from ..models import Event, get_session
from ..services.insight import (
    INSIGHT_PROMPT_VERSION,
    InsightParseError,
    begin_generation,
    build_prompt,
    complete_generation,
    fail_generation,
    load_materials,
    parse_insight_response,
)
from ..services.llm_client import LLMError, get_llm_client
from .celery_app import celery_app

_logger = logging.getLogger(__name__)

# LLM 未配置时的用户可读错误（落 error 列，GET 返回给客户端展示）
_LLM_NOT_CONFIGURED = "透视服务未配置 LLM，请联系运营"


def _load_event(session, event_id: int) -> Event | None:
    """独立接缝便于单测 monkeypatch（Event 含 pgvector 列，SQLite 不能建表）。"""
    return session.get(Event, event_id)


def generate_insight(event_id: int, *, now: datetime | None = None) -> dict:
    """生成某事件的热点透视（pending/generating → ready/failed）。

    返回摘要 dict：status（ready/failed/skipped）+ 失败时的 error。
    """
    now = now or datetime.now(timezone.utc)

    # 1) 入口幂等：仅 pending/generating 行可被认领；ready/failed/被删 → 跳过
    with get_session() as s:
        if not begin_generation(s, event_id, now):
            return {"event_id": event_id, "status": "skipped"}

    try:
        # 2) 加载素材（事件中途被删 → 静默结束）
        with get_session() as s:
            event = _load_event(s, event_id)
            if event is None:
                return {"event_id": event_id, "status": "skipped"}
            materials = load_materials(s, event, now=now)

        # 3) 调 LLM（未配置 / 调用失败 / 解析失败 → failed，见 except）
        if not settings.llm.enabled:
            raise LLMError(_LLM_NOT_CONFIGURED)
        prompt = build_prompt(materials)
        resp = get_llm_client().chat_json(prompt, temperature=0.3)
        sections = parse_insight_response(resp)

        # 4) 完成写库（仅 generating 行可翻 ready；行被删则 rowcount=0 no-op）
        llm_meta = {
            "model": resp.model,
            "usage": resp.usage,
            "cost": resp.cost,
            "temperature": 0.3,
            "prompt_version": INSIGHT_PROMPT_VERSION,
            "fingerprint": materials["fingerprint"],
        }
        done_at = datetime.now(timezone.utc)
        with get_session() as s:
            ok = complete_generation(
                s,
                event_id,
                sections=sections,
                related_ids=materials["related_ids"],
                llm_meta=llm_meta,
                now=done_at,
            )
        _logger.info("[insight] event=%s 生成完成（写库=%s）", event_id, ok)
        return {"event_id": event_id, "status": "ready", "persisted": ok}

    except (LLMError, InsightParseError, json.JSONDecodeError) as e:
        # 用户可重试的失败：落 failed 不上抛
        error = str(e)[:512]
        fail_at = datetime.now(timezone.utc)
        with get_session() as s:
            fail_generation(s, event_id, error, fail_at)
        _logger.warning("[insight] event=%s 生成失败：%s", event_id, error[:200])
        return {"event_id": event_id, "status": "failed", "error": error[:200]}


@celery_app.task(
    name="content_engine.tasks.insight_tasks.generate_insight",
    soft_time_limit=300,
    time_limit=360,
)
def generate_insight_task(event_id: int) -> dict:
    """POST /event/{id}/insight 的异步入队入口。"""
    return generate_insight(event_id)


__all__ = ["generate_insight", "generate_insight_task"]
