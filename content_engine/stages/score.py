"""阶段 6：评分（score）。

阶段 0 实现（公式与 pipeline_demo 一致，方案 §7.1）：
    importance = 0.4·来源等级权重(max)
               + 0.3·min(distinct_sources / 5, 1)
               + 0.2·hotness
               + 0.1·recency（exp(-Δt/τ)，τ=settings.threshold.recency_tau_hours）

阶段 3.3（本次）：把占位的 ``hotness=0.5`` 换成真实热度信号——
    hotness = 0.5·cross_source       跨源数 min(distinct/5, 1)，多源报道 = 热
            + 0.3·social_ratio       B 级（社交/自媒体）成员占比，代表"舆论声量"
            + 0.2·freshness_velocity 24h 内成员占比，代表"还在持续发酵"
回写 event.hotness（供 /feed 卡片展示），importance 公式不变。

阶段 3.4（本次）：评分结束把 scored 事件写入 Redis ZSet 榜单（全站 + 分模块）。

推进状态：summarized → scored。
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import select

from content_engine.config import settings
from content_engine.models import Event, EventStatus, SourceLevel, get_session
from content_engine.services import ranking

from .seed_data import LEVEL_WEIGHT

# 24h 内更新视为"仍在发酵"
_FRESHNESS_WINDOW_HOURS = 24


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


def compute_hotness(members, now: datetime | None = None) -> float:
    """真实热度信号，返回 [0, 1]。

    members：EventArticle 链接列表（含 .article.source / .article.fetched_at）。
    """
    if not members:
        return 0.0
    now = now or datetime.now(timezone.utc)

    # 1) 跨源数：多家媒体同时报道 = 热度高
    distinct = len({link.article.source_id for link in members})
    cross_source = min(distinct / 5, 1.0)

    # 2) 社交/自媒体声量：B 级成员占比
    b_count = sum(
        1
        for link in members
        if (link.article.source.level if link.article.source else SourceLevel.B) == SourceLevel.B
    )
    social_ratio = b_count / len(members)

    # 3) 持续发酵：24h 内 fetched 的成员占比
    fresh = 0
    for link in members:
        fetched = link.article.fetched_at
        if fetched is None:
            continue
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if (now - fetched).total_seconds() / 3600 <= _FRESHNESS_WINDOW_HOURS:
            fresh += 1
    freshness_velocity = fresh / len(members)

    hotness = 0.5 * cross_source + 0.3 * social_ratio + 0.2 * freshness_velocity
    return round(max(0.0, min(1.0, hotness)), 4)


def score_event(event: Event) -> float:
    members = event.article_links
    level_w = _level_weight(members)
    distinct = len({link.article.source_id for link in members})
    cross = min(distinct / 5, 1.0)
    hotness = compute_hotness(members)
    event.hotness = hotness  # 回写真实热度，供 /feed 展示
    recency = _recency(event.last_update)
    importance = 0.4 * level_w + 0.3 * cross + 0.2 * hotness + 0.1 * recency
    return round(importance * 100, 1)


def run() -> dict:
    stats = {"scored": 0, "ranked": 0}
    ranked_rows: list[tuple[int, str, float]] = []
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
            ranked_rows.append((ev.id, ev.module.value, ev.importance))
            stats["scored"] += 1

    # 阶段 3.4：写 Redis 榜单（可降级——Redis 不可用时静默跳过）
    if ranked_rows:
        stats["ranked"] = ranking.rebuild(ranked_rows)

    print(f"  [score] {stats['scored']} 个事件已评分  榜单写入 {stats['ranked']} 条")
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 6/6] score 评分")
    print("=" * 60)
    print(run())
