"""阶段 4：去重 + 事件聚类（cluster）。

阶段 0：72h 时间窗 + Jaccard ≥ 0.86 并入或新建事件。
阶段 2.3（本次）：用 Embedding 质心余弦做增量聚类，Jaccard 仅作 embedding 缺失时的兜底。

算法：
1. 取出 status=classified 的文章，按 fetched_at 顺序流式处理；
2. 时间窗 72h 内、同 module 的现有事件，按 ``Event.centroid`` 做 cos 检索；
3. 找到 cos ≥ ``settings.threshold.cluster_threshold``（0.86）的最佳事件 → 并入：
   - 写 EventArticle(similarity=cos)
   - 增量更新 centroid = (centroid * n + emb) / (n + 1)（再做 L2 归一化）
   - 更新 last_update / source_count
4. 找不到则新建事件，centroid = 当前文章 embedding。
5. 文章无 embedding（embed_failed 兜底过来的）→ 退化走旧 Jaccard 路径。

阶段 2.2 待审队列：
- 事件创建/更新后，如果该事件成员中 cls_confidence < 阈值的占比 > 50%，
  初始置 ``EventStatus.reviewing``；summarize 阶段会跳过 reviewing 事件，
  人工 approve 后重置回 clustered 再继续生产。

阶段 2.4 离线 HDBSCAN 复核见 [recluster.py](./recluster.py)。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

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

# 待审阈值：成员中 cls_confidence < cls_llm_threshold 的占比超过此值则进 reviewing
_REVIEW_LOW_CONF_RATIO = 0.5


def _cos(a: list[float], b: list[float]) -> float:
    """两向量余弦相似度；任一为空或全零返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / ((na**0.5) * (nb**0.5))


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _update_centroid(old: list[float], n: int, new_vec: list[float]) -> list[float]:
    """增量更新质心：先按数量加权平均，再 L2 归一化。"""
    merged = [(o * n + v) / (n + 1) for o, v in zip(old, new_vec, strict=True)]
    return _normalize(merged)


def _max_jaccard(article: RawArticle, members: list[RawArticle]) -> float:
    a_text = f"{article.title} {article.content}"
    return max(jaccard(a_text, f"{m.title} {m.content}") for m in members) if members else 0.0


def _maybe_review(event: Event, members: list[RawArticle]) -> EventStatus:
    """根据成员置信度判定事件初始状态：低置信占比过半 → reviewing。"""
    if not members:
        return EventStatus.clustered
    threshold = settings.threshold.cls_llm_threshold
    low = sum(1 for m in members if (m.cls_confidence or 0.0) < threshold)
    if low / len(members) > _REVIEW_LOW_CONF_RATIO:
        return EventStatus.reviewing
    return EventStatus.clustered


def run() -> dict:
    cos_threshold = settings.threshold.cluster_threshold
    window = timedelta(hours=settings.threshold.cluster_window_hours)
    stats = {
        "events_new": 0,
        "events_grow": 0,
        "articles_processed": 0,
        "matched_by_centroid": 0,
        "matched_by_jaccard": 0,
        "review_queue": 0,
    }

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

        cutoff = datetime.now(timezone.utc) - window
        active_events = (
            s.execute(
                select(Event).where(Event.last_update >= cutoff)
            )
            .scalars()
            .all()
        )
        # 缓存每个事件的成员（同会话内可读 .article_links）
        event_members: dict[int, list[RawArticle]] = {
            ev.id: [link.article for link in ev.article_links] for ev in active_events
        }

        for art in articles:
            now = datetime.now(timezone.utc)
            art_emb = list(art.embedding) if art.embedding is not None else None

            best_event: Event | None = None
            best_sim = 0.0
            matched_path = "centroid"

            # 主路径：embedding 质心 cos 检索
            if art_emb is not None:
                for ev in active_events:
                    if ev.module != art.module:
                        continue
                    if ev.centroid is None:
                        continue
                    sim = _cos(art_emb, list(ev.centroid))
                    if sim >= cos_threshold and sim > best_sim:
                        best_event = ev
                        best_sim = sim

            # 兜底：embedding 缺失时退化走 Jaccard（与阶段 0 一致）
            if best_event is None and art_emb is None:
                jac_threshold = cos_threshold
                for ev in active_events:
                    if ev.module != art.module:
                        continue
                    sim = _max_jaccard(art, event_members.get(ev.id, []))
                    if sim >= jac_threshold and sim > best_sim:
                        best_event = ev
                        best_sim = sim
                if best_event is not None:
                    matched_path = "jaccard"

            if best_event is not None:
                # 并入：写关联 + 更新质心 + 更新元数据
                s.add(
                    EventArticle(
                        event_id=best_event.id, article_id=art.id, similarity=best_sim
                    )
                )
                members = event_members[best_event.id]
                if art_emb is not None and best_event.centroid is not None:
                    best_event.centroid = _update_centroid(
                        list(best_event.centroid), len(members), art_emb
                    )
                elif art_emb is not None and best_event.centroid is None:
                    best_event.centroid = _normalize(art_emb)
                best_event.source_count = len(
                    {m.source_id for m in members} | {art.source_id}
                )
                best_event.last_update = now
                # 若已进入 reviewing，新增成员不改其状态（人工决定）
                members.append(art)
                if matched_path == "centroid":
                    stats["matched_by_centroid"] += 1
                else:
                    stats["matched_by_jaccard"] += 1
                stats["events_grow"] += 1
            else:
                # 新建事件：质心 = 当前文章 embedding（已 L2 归一化）
                ev = Event(
                    module=art.module or Module.tech,
                    centroid=_normalize(art_emb) if art_emb is not None else None,
                    source_count=1,
                    hotness=0.5,
                    importance=0.0,
                    first_seen=art.published_at or now,
                    last_update=now,
                    status=EventStatus.clustered,
                )
                s.add(ev)
                s.flush()
                s.add(EventArticle(event_id=ev.id, article_id=art.id, similarity=1.0))
                active_events.append(ev)
                event_members[ev.id] = [art]
                stats["events_new"] += 1

            art.status = ArticleStatus.clustered
            stats["articles_processed"] += 1

        # 事件级低置信判定（仅对本轮有变更的事件做一次评估）
        touched_ids = {ev.id for ev in active_events if ev.id is not None}
        for ev in active_events:
            if ev.id not in touched_ids:
                continue
            if ev.status not in (EventStatus.clustered, EventStatus.reviewing):
                continue
            new_status = _maybe_review(ev, event_members.get(ev.id, []))
            if new_status == EventStatus.reviewing and ev.status != EventStatus.reviewing:
                ev.status = EventStatus.reviewing
                stats["review_queue"] += 1

    print(
        f"  [cluster] 处理 {stats['articles_processed']} 条 → "
        f"新事件 {stats['events_new']}  追加 {stats['events_grow']} "
        f"(centroid={stats['matched_by_centroid']} jaccard={stats['matched_by_jaccard']})  "
        f"待审 {stats['review_queue']}"
    )
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 4/6] cluster 去重+聚类")
    print("=" * 60)
    print(run())
