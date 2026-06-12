"""阶段 2.4：HDBSCAN 离线复核（recluster）。

每日 cron 调度一次：
1. 取最近 ``window_hours``（默认 168h = 7 天）内 status ∈
   {summarized, scored, published} 的事件成员（带 embedding 向量）；
2. 用 sklearn HDBSCAN（metric='cosine', min_cluster_size=2）批量聚类；
3. 与现有事件归属比对，仅"打标"建议（不改 event_articles，避免破坏可回溯）：
   - **拆**：同事件成员被分到 ≥2 个 HDBSCAN 簇 → 标 ``events.needs_split=True``
   - **并**：跨事件成员落入同一 HDBSCAN 簇 → 标较大 id 事件的
     ``suggested_merge_id`` 指向较小 id；
4. 输出报告 dict（events_marked_split / events_marked_merge / total_events / noise）。

为什么不直接改 event_articles：
- 铁律「可回溯」：自动调整聚类会让人工无法追溯改动来源；
- HDBSCAN 在小样本/向量异常下容易误判；
- 阶段 4.2 CMS 接入后由人工在后台批量合并/拆分。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from content_engine.models import (
    Event,
    EventArticle,
    EventStatus,
    RawArticle,
    get_session,
)

# 触发 needs_split 的最低成员阈值：单成员事件无意义
_MIN_MEMBERS_FOR_SPLIT = 2
# 复核窗口（小时）；实施计划 §3 提到「每日 HDBSCAN 离线复核」，默认 7 天足够
_DEFAULT_WINDOW_HOURS = 168


def _load_dataset(session, cutoff: datetime) -> tuple[list[Event], dict[int, list[tuple[int, list[float]]]]]:
    """取最近窗口内 summarized+ 状态的事件成员。

    Returns:
        events: 事件列表
        members: {event_id: [(article_id, embedding), ...]}（仅含有 embedding 的成员）
    """
    events = (
        session.execute(
            select(Event)
            .where(Event.last_update >= cutoff)
            .where(
                Event.status.in_(
                    [EventStatus.summarized, EventStatus.scored, EventStatus.published]
                )
            )
        )
        .scalars()
        .all()
    )
    if not events:
        return [], {}

    event_ids = [ev.id for ev in events]
    rows = session.execute(
        select(EventArticle.event_id, RawArticle.id, RawArticle.embedding)
        .join(RawArticle, RawArticle.id == EventArticle.article_id)
        .where(EventArticle.event_id.in_(event_ids))
        .where(RawArticle.embedding.is_not(None))
    ).all()

    members: dict[int, list[tuple[int, list[float]]]] = {ev.id: [] for ev in events}
    for ev_id, art_id, emb in rows:
        members[ev_id].append((art_id, list(emb)))
    return events, members


def _run_hdbscan(vectors: list[list[float]], min_cluster_size: int = 2) -> list[int]:
    """运行 HDBSCAN；返回每个向量的簇标签（-1 表示噪声）。"""
    # 延迟导入：sklearn 不是默认依赖（仅复核任务需要）
    import numpy as np
    from sklearn.cluster import HDBSCAN

    X = np.asarray(vectors, dtype=float)
    model = HDBSCAN(min_cluster_size=min_cluster_size, metric="cosine")
    labels = model.fit_predict(X)
    return labels.tolist()


def run(window_hours: int = _DEFAULT_WINDOW_HOURS, dry_run: bool = False) -> dict:
    """执行一次离线复核。

    Args:
        window_hours: 取最近 N 小时内的事件成员
        dry_run: 仅输出报告，不写库（CI / 手工排查时用）
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    stats = {
        "events_total": 0,
        "members_total": 0,
        "events_marked_split": 0,
        "events_marked_merge": 0,
        "noise": 0,
        "dry_run": dry_run,
    }

    with get_session() as s:
        events, members = _load_dataset(s, cutoff)
        if not events:
            print("  [recluster] 窗口内无可复核事件")
            return stats

        # 平铺所有 (article_id, event_id, embedding)，跑一次 HDBSCAN
        flat: list[tuple[int, int, list[float]]] = []
        for ev_id, items in members.items():
            for art_id, emb in items:
                flat.append((art_id, ev_id, emb))
        if len(flat) < _MIN_MEMBERS_FOR_SPLIT:
            print(f"  [recluster] 样本太少 ({len(flat)})，跳过")
            return stats

        stats["events_total"] = len(events)
        stats["members_total"] = len(flat)

        labels = _run_hdbscan([emb for _, _, emb in flat])

        # event_id → set(label)
        ev_labels: dict[int, set[int]] = {}
        # label → set(event_id)
        label_evs: dict[int, set[int]] = {}
        for (art_id, ev_id, _), lab in zip(flat, labels, strict=True):
            if lab == -1:
                stats["noise"] += 1
                continue
            ev_labels.setdefault(ev_id, set()).add(lab)
            label_evs.setdefault(lab, set()).add(ev_id)

        # 拆建议：同事件落到 >1 个簇
        ev_by_id = {ev.id: ev for ev in events}
        for ev_id, labs in ev_labels.items():
            if len(labs) >= 2:
                ev = ev_by_id[ev_id]
                if not dry_run:
                    ev.needs_split = True
                stats["events_marked_split"] += 1

        # 并建议：同簇横跨多事件 → 把较大 id 的 suggested_merge_id 指向最小 id
        for lab, ev_ids in label_evs.items():
            if len(ev_ids) < 2:
                continue
            target = min(ev_ids)
            for ev_id in ev_ids:
                if ev_id == target:
                    continue
                ev = ev_by_id[ev_id]
                if not dry_run:
                    ev.suggested_merge_id = target
                stats["events_marked_merge"] += 1

    print(
        f"  [recluster] events={stats['events_total']}  members={stats['members_total']}  "
        f"split↑{stats['events_marked_split']}  merge↑{stats['events_marked_merge']}  "
        f"noise={stats['noise']}{'  (dry-run)' if dry_run else ''}"
    )
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 2.4] recluster HDBSCAN 离线复核")
    print("=" * 60)
    print(run())
