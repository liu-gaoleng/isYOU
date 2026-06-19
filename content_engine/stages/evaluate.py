"""A 阶段 / M1 Gate 硬指标评测：分类准确率 + 串卡率（误并 / 漏并）。

设计（对齐已对齐口径）：
- 分类准确率 → 人工标注：先 ``sample-classify`` 分层抽样导出 CSV（含预测模块 +
  待人工填写的 ``true_module`` 空列），人工填好后用 ``score-classify`` 读回算准确率
  与逐模块 precision/recall + 混淆矩阵。
- 串卡率 → embedding 自动评测（无需人工 / 无需 LLM）：
  - 误并率：同一事件内成员文章与事件质心 cos < 阈值 的占比（簇内不纯）；
  - 漏并率：同模块、时间窗内、质心 cos ≥ 阈值 的不同事件占比（本该合并却分立）。

所有 cos 阈值默认取 ``settings.threshold.cluster_threshold``（0.86），与 cluster 阶段一致。

用法：
    python -m content_engine.stages.evaluate sample-classify --per-module 25 --out sample.csv
    # 人工在 sample.csv 的 true_module 列填四选一（tech/finance/ai/macro），留空=跳过
    python -m content_engine.stages.evaluate score-classify --in sample.csv
    python -m content_engine.stages.evaluate cluster
    python -m content_engine.stages.evaluate gate   # 串卡自动指标一把出（分类需先标注）
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from content_engine.config import settings
from content_engine.logging_config import get_logger
from content_engine.models import (
    Event,
    EventArticle,
    EventStatus,
    Module,
    RawArticle,
    get_session,
)

from .cluster import _cos

_logger = get_logger(__name__)

_CSV_FIELDS = [
    "article_id",
    "source",
    "predicted_module",
    "cls_confidence",
    "true_module",  # 人工填：tech/finance/ai/macro，留空=跳过
    "title",
    "excerpt",
]
_EXCERPT_CHARS = 200

# 已发布产物抽检（§1.1 连续 3 天日更）：抽样单位是已发布事件，
# 人工对照 card/detail 摘要判断模块归类是否正确（true_module 留空=正确，仅误判行填正确模块），
# 复用 score-classify 的「只标错误」口径打合格率。
_PUB_CSV_FIELDS = [
    "event_id",
    "published_date",
    "predicted_module",
    "importance",
    "source_count",
    "true_module",  # 人工填：tech/finance/ai/macro，留空=预测正确（blank-correct 口径）
    "card_summary",
    "detail_excerpt",
]


def _stratified_pick(
    by_module: dict[str, list],
    per_module: int,
    rng: random.Random,
) -> list:
    """按四模块分层、每模块随机抽 ``per_module`` 条（不足则全取）。纯函数，便于单测。"""
    picked: list = []
    for module in (m.value for m in Module):
        pool = list(by_module.get(module, []))
        rng.shuffle(pool)
        picked.extend(pool[:per_module])
    return picked


# ---------------------------------------------------------------------------
# 分类：分层抽样导出
# ---------------------------------------------------------------------------
def sample_classify(per_module: int, out_path: str, seed: int = 42) -> dict:
    """按模块分层抽样已分类文章，导出 CSV 供人工标注。"""
    rng = random.Random(seed)
    by_module: dict[str, list[RawArticle]] = defaultdict(list)
    with get_session() as s:
        rows = (
            s.execute(select(RawArticle).where(RawArticle.module.is_not(None)))
            .scalars()
            .all()
        )
        for art in rows:
            by_module[art.module.value].append(art)

        picked: list[RawArticle] = []
        for module in (m.value for m in Module):
            pool = by_module.get(module, [])
            rng.shuffle(pool)
            picked.extend(pool[:per_module])

        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            for art in picked:
                writer.writerow(
                    {
                        "article_id": art.id,
                        "source": art.source.name if art.source else "",
                        "predicted_module": art.module.value if art.module else "",
                        "cls_confidence": (
                            f"{art.cls_confidence:.2f}" if art.cls_confidence is not None else ""
                        ),
                        "true_module": "",
                        "title": (art.title or "").replace("\n", " "),
                        "excerpt": (art.content or "")[:_EXCERPT_CHARS].replace("\n", " "),
                    }
                )

    stats = {"sampled": len(picked), "by_module": {m: min(len(by_module.get(m, [])), per_module) for m in (x.value for x in Module)}, "out": out_path}
    _logger.info("[eval.sample] 导出 %d 条到 %s  分布=%s", stats["sampled"], out_path, stats["by_module"])
    return stats


# ---------------------------------------------------------------------------
# 分类：读回人工标注算准确率
# ---------------------------------------------------------------------------
def score_classify(in_path: str, blank_correct: bool = False) -> dict:
    """读回已标注 CSV，算总体准确率 + 逐模块 precision/recall + 混淆矩阵。

    两种标注口径：
    - 默认（全量标注）：仅统计 ``true_module`` 已填且合法的行，空白=跳过；
    - ``blank_correct=True``（只标错误）：``true_module`` 空白视为「预测正确」，
      即 true=predicted；只在误分类行填写正确模块。此时分母为全部有合法预测的行。
    """
    valid = {m.value for m in Module}
    labeled: list[tuple[str, str]] = []  # (predicted, true)
    with open(in_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            true = (row.get("true_module") or "").strip().lower()
            pred = (row.get("predicted_module") or "").strip().lower()
            if pred not in valid:
                continue
            if true in valid:
                labeled.append((pred, true))
            elif blank_correct and not true:
                # 只标错误口径：空白 = 预测正确
                labeled.append((pred, pred))

    n = len(labeled)
    if n == 0:
        _logger.warning("[eval.score] %s 中没有有效的人工标注（true_module 为空？）", in_path)
        return {"labeled": 0, "accuracy": None}

    correct = sum(1 for p, t in labeled if p == t)
    accuracy = round(correct / n, 4)

    # 混淆矩阵 + 逐模块 P/R
    confusion: dict[str, dict[str, int]] = {t: defaultdict(int) for t in valid}
    tp = defaultdict(int)
    pred_total = defaultdict(int)
    true_total = defaultdict(int)
    for p, t in labeled:
        confusion[t][p] += 1
        pred_total[p] += 1
        true_total[t] += 1
        if p == t:
            tp[t] += 1

    per_module = {}
    for m in valid:
        precision = round(tp[m] / pred_total[m], 4) if pred_total[m] else None
        recall = round(tp[m] / true_total[m], 4) if true_total[m] else None
        per_module[m] = {"precision": precision, "recall": recall, "support": true_total[m]}

    result = {
        "labeled": n,
        "correct": correct,
        "accuracy": accuracy,
        "per_module": per_module,
        "confusion": {t: dict(confusion[t]) for t in valid},
    }
    _logger.info("[eval.score] n=%d  准确率=%.2f%%", n, accuracy * 100)
    return result


# ---------------------------------------------------------------------------
# 已发布产物抽检（§1.1 连续 3 天日更 → 抽检 ≥90%）
# ---------------------------------------------------------------------------
def sample_published(
    per_module: int,
    out_path: str,
    days: int = 3,
    seed: int = 42,
    now: datetime | None = None,
) -> dict:
    """按「最近 ``days`` 天发布窗口 + status=published + 四模块分层」抽样已发布事件导出 CSV。

    与 ``sample_classify`` 的区别：抽样单位是**已发布事件**而非全量已分类文章，且限定
    在灰度日更窗口内（按 ``Event.last_update`` 作发布日口径），精确对齐 §1.1
    「连续 3 天日更产出抽检」。导出后人工对照 card/detail 摘要判断模块是否归类正确，
    用 ``score-classify --blank-correct``（只标错误口径）算合格率。
    """
    rng = random.Random(seed)
    ref = now or datetime.now(timezone.utc)
    since = ref - timedelta(days=days)

    by_module: dict[str, list[Event]] = defaultdict(list)
    with get_session() as s:
        rows = (
            s.execute(
                select(Event).where(
                    Event.status == EventStatus.published,
                    Event.last_update >= since,
                )
            )
            .scalars()
            .all()
        )
        for ev in rows:
            by_module[ev.module.value].append(ev)

        picked = _stratified_pick(by_module, per_module, rng)

        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=_PUB_CSV_FIELDS)
            writer.writeheader()
            for ev in picked:
                writer.writerow(
                    {
                        "event_id": ev.id,
                        "published_date": ev.last_update.date().isoformat()
                        if ev.last_update
                        else "",
                        "predicted_module": ev.module.value,
                        "importance": f"{ev.importance:.3f}"
                        if ev.importance is not None
                        else "",
                        "source_count": ev.source_count,
                        "true_module": "",
                        "card_summary": (ev.card_summary or "").replace("\n", " "),
                        "detail_excerpt": (ev.detail_summary or "")[:_EXCERPT_CHARS].replace(
                            "\n", " "
                        ),
                    }
                )

    by_module_counts = {
        m.value: min(len(by_module.get(m.value, [])), per_module) for m in Module
    }
    available = {m.value: len(by_module.get(m.value, [])) for m in Module}
    stats = {
        "sampled": len(picked),
        "window_days": days,
        "since": since.isoformat(),
        "by_module": by_module_counts,
        "available_by_module": available,
        "out": out_path,
    }
    _logger.info(
        "[eval.sample-published] 导出 %d 条到 %s  窗口=%d天  分布=%s  库存=%s",
        stats["sampled"],
        out_path,
        days,
        by_module_counts,
        available,
    )
    return stats


# ---------------------------------------------------------------------------
# 串卡：误并 + 漏并（embedding 自动）
# ---------------------------------------------------------------------------
def eval_cluster(threshold: float | None = None) -> dict:
    """误并率（簇内不纯）+ 漏并率（应并未并），全部用 embedding 自动评测。"""
    thr = threshold if threshold is not None else settings.threshold.cluster_threshold
    window = timedelta(hours=settings.threshold.cluster_window_hours)

    with get_session() as s:
        # ---- 误并：逐事件，成员文章与质心 cos < 阈值 视为错并 ----
        multi_event_ids = (
            s.execute(
                select(EventArticle.event_id)
                .group_by(EventArticle.event_id)
                .having(func.count() >= 2)
            )
            .scalars()
            .all()
        )
        misjoin_members = 0
        misjoin_total_members = 0
        misjoin_events = 0
        for eid in multi_event_ids:
            ev = s.get(Event, eid)
            if ev is None or ev.centroid is None:
                continue
            centroid = list(ev.centroid)
            links = ev.article_links
            event_bad = False
            for link in links:
                art = link.article
                if art.embedding is None:
                    continue
                misjoin_total_members += 1
                if _cos(list(art.embedding), centroid) < thr:
                    misjoin_members += 1
                    event_bad = True
            if event_bad:
                misjoin_events += 1

        misjoin_rate = (
            round(misjoin_members / misjoin_total_members, 4) if misjoin_total_members else 0.0
        )

        # ---- 漏并：同模块 + 时间窗内 + 质心 cos ≥ 阈值 的不同事件对 ----
        events = (
            s.execute(select(Event).where(Event.centroid.is_not(None)))
            .scalars()
            .all()
        )
        by_module: dict[Module, list[Event]] = defaultdict(list)
        for ev in events:
            by_module[ev.module].append(ev)

        considered = 0
        events_with_dup = 0
        missed_pairs = 0
        pair_total = 0
        for module, evs in by_module.items():
            for i, a in enumerate(evs):
                considered += 1
                has_dup = False
                for b in evs[i + 1 :]:
                    # 时间窗：两事件 last_update 相差在窗口内才算可比对
                    if abs((a.last_update - b.last_update).total_seconds()) > window.total_seconds():
                        continue
                    pair_total += 1
                    if _cos(list(a.centroid), list(b.centroid)) >= thr:
                        missed_pairs += 1
                        has_dup = True
                if has_dup:
                    events_with_dup += 1

        missed_join_rate = round(events_with_dup / considered, 4) if considered else 0.0

    result = {
        "threshold": thr,
        "misjoin": {
            "multi_article_events": len(multi_event_ids),
            "members_checked": misjoin_total_members,
            "members_misjoined": misjoin_members,
            "events_with_misjoin": misjoin_events,
            "misjoin_rate": misjoin_rate,
        },
        "missed_join": {
            "events_considered": considered,
            "comparable_pairs": pair_total,
            "missed_pairs": missed_pairs,
            "events_with_duplicate": events_with_dup,
            "missed_join_rate": missed_join_rate,
        },
    }
    _logger.info(
        "[eval.cluster] thr=%.2f  误并率=%.2f%%（%d/%d 成员）  漏并率=%.2f%%（%d/%d 事件）",
        thr,
        misjoin_rate * 100,
        misjoin_members,
        misjoin_total_members,
        missed_join_rate * 100,
        events_with_dup,
        considered,
    )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="M1 Gate 评测：分类准确率 + 串卡率")
    sub = p.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("sample-classify", help="分层抽样导出人工标注 CSV")
    s1.add_argument("--per-module", type=int, default=25)
    s1.add_argument("--out", default="eval_classify_sample.csv")
    s1.add_argument("--seed", type=int, default=42)

    s2 = sub.add_parser("score-classify", help="读回已标注 CSV 算准确率")
    s2.add_argument("--in", dest="in_path", required=True)
    s2.add_argument(
        "--blank-correct",
        action="store_true",
        help="只标错误口径：true_module 空白视为预测正确（只在误分类行填正确模块）",
    )

    s3 = sub.add_parser(
        "sample-published",
        help="按发布窗口分层抽样已发布事件（§1.1 连续 3 天日更产出抽检）",
    )
    s3.add_argument("--per-module", type=int, default=25)
    s3.add_argument("--days", type=int, default=3, help="发布日窗口天数（默认 3）")
    s3.add_argument("--out", default="eval_published_sample.csv")
    s3.add_argument("--seed", type=int, default=42)

    sub.add_parser("cluster", help="自动评测误并 + 漏并率")
    sub.add_parser("gate", help="串卡自动指标汇总（分类需先人工标注）")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.cmd == "sample-classify":
        print(sample_classify(args.per_module, args.out, args.seed))
    elif args.cmd == "sample-published":
        print(
            sample_published(
                args.per_module, args.out, days=args.days, seed=args.seed
            )
        )
    elif args.cmd == "score-classify":
        import json

        print(
            json.dumps(
                score_classify(args.in_path, blank_correct=args.blank_correct),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.cmd in ("cluster", "gate"):
        import json

        print(json.dumps(eval_cluster(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
