"""阶段 3：分类（classify）。

阶段 0 起步：纯关键词规则（与 pipeline_demo 一致），命中分数最高的模块即为分类结果，
全 0 时回退到信源默认 module。状态推进 cleaned → classified。

阶段 2.1 会在此处叠加 LLM 分类，对 confidence < 0.6 的兜底进 LLM。
"""

from __future__ import annotations

from sqlalchemy import select

from content_engine.models import ArticleStatus, Module, RawArticle, get_session

from .seed_data import CLASSIFY_RULES


def classify_one(title: str, content: str, fallback: Module) -> tuple[Module, float]:
    """关键词打分，返回 (模块, 置信度 0-1)。"""
    text = f"{title} {content}".lower()
    scores: dict[Module, int] = {}
    for module, kws in CLASSIFY_RULES.items():
        scores[module] = sum(1 for kw in kws if kw.lower() in text)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return fallback, 0.5
    total = sum(scores.values())
    return best, round(scores[best] / total, 2) if total else 0.5


def run() -> dict:
    stats = {"classified": 0, "by_module": {}}
    with get_session() as s:
        rows = (
            s.execute(
                select(RawArticle).where(RawArticle.status == ArticleStatus.cleaned)
            )
            .scalars()
            .all()
        )
        for art in rows:
            fallback = art.source.module if art.source else Module.tech
            module, conf = classify_one(art.title, art.content, fallback)
            art.module = module
            art.cls_confidence = conf
            art.status = ArticleStatus.classified
            stats["by_module"][module.value] = stats["by_module"].get(module.value, 0) + 1
            stats["classified"] += 1
    print(f"  [classify] {stats['classified']} 条  分布={stats['by_module']}")
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 3/6] classify 分类")
    print("=" * 60)
    print(run())
