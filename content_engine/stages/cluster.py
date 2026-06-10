"""阶段 4：去重 + 事件聚类（cluster）。

阶段 0 实现（与 pipeline_demo 算法一致）：
1. 精确去重（DB 端已由 raw_hash + url 唯一约束兜底，此处作语义近重补漏）；
2. 增量聚类：对 status=classified 的文章，按 module 找 72h 内现有事件，
   用 Jaccard(标题+正文 vs 簇内任一成员) ≥ cluster_threshold 即并入，否则新建事件。
3. 推进状态：classified → clustered，并写 event_articles 关联。

阶段 2.3/2.4 会替换为 Embedding 余弦 + HDBSCAN 复核。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select

from content_engine.config import settings
from content_engine.models import (
    ArticleStatus,
    Event,
    EventArticle,
    EventStatus,
    Module,
    RawArticle,
    get_session,
)

from .utils import jaccard


def _max_similarity(article: RawArticle, members: list[RawArticle]) -> float:
    a_text = f"{article.title} {article.content}"
    return max(jaccard(a_text, f"{m.title} {m.content}") for m in members) if members else 0.0


def run() -> dict:
    threshold = settings.threshold.cluster_threshold
    window = timedelta(hours=settings.threshold.cluster_window_hours)
    stats = {"events_new": 0, "events_grow": 0, "articles_processed": 0}

    with get_session() as s:
        articles = (
            s.execute(
                select(RawArticle)
                .where(RawArticle.status == ArticleStatus.classified)
                .order_by(RawArticle.fetched_at.asc())
            )
            .scalars()
            .all()
        )

        # 时间窗内的活跃事件（避免遍历全表）
        cutoff = datetime.now(timezone.utc) - window
        active_events = (
            s.execute(
                select(Event).where(Event.last_update >= cutoff)
            )
            .scalars()
            .all()
        )
        # 缓存每个事件的现有成员（同一会话内）
        event_members: dict[int, list[RawArticle]] = {
            ev.id: [link.article for link in ev.article_links] for ev in active_events
        }

        for art in articles:
            now = datetime.now(timezone.utc)
            best_event = None
            best_sim = 0.0
            for ev in active_events:
                if ev.module != art.module:
                    continue
                sim = _max_similarity(art, event_members.get(ev.id, []))
                if sim >= threshold and sim > best_sim:
                    best_event = ev
                    best_sim = sim

            if best_event is not None:
                # 并入现有事件
                s.add(EventArticle(event_id=best_event.id, article_id=art.id, similarity=best_sim))
                best_event.source_count = len({m.source_id for m in event_members[best_event.id]} | {art.source_id})
                best_event.last_update = now
                event_members[best_event.id].append(art)
                stats["events_grow"] += 1
            else:
                # 新建事件
                ev = Event(
                    module=art.module or Module.tech,
                    source_count=1,
                    hotness=0.5,
                    importance=0.0,
                    first_seen=art.published_at or now,
                    last_update=now,
                    status=EventStatus.clustered,
                )
                s.add(ev)
                s.flush()  # 拿到 ev.id
                s.add(EventArticle(event_id=ev.id, article_id=art.id, similarity=1.0))
                active_events.append(ev)
                event_members[ev.id] = [art]
                stats["events_new"] += 1

            art.status = ArticleStatus.clustered
            stats["articles_processed"] += 1

    print(
        f"  [cluster] 处理 {stats['articles_processed']} 条 → "
        f"新事件 {stats['events_new']}  追加 {stats['events_grow']}"
    )
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 4/6] cluster 去重+聚类")
    print("=" * 60)
    print(run())
