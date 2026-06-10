"""阶段 6：评分（score）。

阶段 0 实现（公式与 pipeline_demo 一致，方案 §7.1）：
    importance = 0.4·来源等级权重(max)
               + 0.3·min(distinct_sources / 5, 1)
               + 0.2·hotness            （阶段 3.3 接真实信号；当前用事件已有的 hotness）
               + 0.1·recency            （exp(-Δt/τ)，τ=settings.threshold.recency_tau_hours）

推进状态：summarized → scored。Redis 榜单留给阶段 3.4。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import select

from content_engine.config import settings
from content_engine.models import Event, EventStatus, SourceLevel, get_session

from .seed_data import LEVEL_WEIGHT


def _level_weight(members) -> float:
    if not members:
        return 0.3
    return max(
        LEVEL_WEIGHT.get(link.article.source.level if link.article.source else SourceLevel.B, 0.3)
        for link in members
    )


def _recency(last_update: datetime) -> float:
    tau = settings.threshold.recency_tau_hours
    if last_update.tzinfo is None:
        last_update = last_update.replace(tzinfo=timezone.utc)
    delta_hours = max((datetime.now(timezone.utc) - last_update).total_seconds() / 3600, 0.0)
    return math.exp(-delta_hours / tau)


def score_event(event: Event) -> float:
    members = event.article_links
    level_w = _level_weight(members)
    distinct = len({link.article.source_id for link in members})
    cross = min(distinct / 5, 1.0)
    hotness = event.hotness  # 阶段 3.3 替换为真实信号
    recency = _recency(event.last_update)
    importance = 0.4 * level_w + 0.3 * cross + 0.2 * hotness + 0.1 * recency
    return round(importance * 100, 1)


def run() -> dict:
    stats = {"scored": 0}
    with get_session() as s:
        events = (
            s.execute(
                select(Event).where(Event.status == EventStatus.summarized)
            )
            .scalars()
            .all()
        )
        for ev in events:
            ev.importance = score_event(ev)
            ev.source_count = len({link.article.source_id for link in ev.article_links})
            ev.status = EventStatus.scored
            stats["scored"] += 1
    print(f"  [score] {stats['scored']} 个事件已评分")
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 6/6] score 评分")
    print("=" * 60)
    print(run())
