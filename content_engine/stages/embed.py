"""阶段 1.3：历史 embedding 回填脚本（embed）。

用途：
- 阶段 1.3 切到 bge-small-zh-v1.5 后，老数据 ``raw_articles.embedding`` 仍为 NULL；
- 这个脚本批量扫描 ``embedding IS NULL AND status IN (cleaned, classified, clustered)`` 的文章，
  分批 encode 后回填，让现有数据可参与下游 cos 比对/聚类；
- 也可作为新文章入库后的「补漏」工具（clean 阶段 embed 失败时会留空，可通过此脚本补回）。

运行：
    DATABASE_URL=postgresql+psycopg://... python -m content_engine.stages.embed
"""

from __future__ import annotations

from sqlalchemy import select

from content_engine.config import settings
from content_engine.models import ArticleStatus, RawArticle, get_session
from content_engine.services.embedding import embed_texts

# 哪些状态的文章值得回填（raw 还没过 clean，先放过；dropped 不回填）
_FILLABLE_STATUSES = (
    ArticleStatus.cleaned,
    ArticleStatus.classified,
    ArticleStatus.clustered,
)


def run() -> dict:
    batch_size = settings.embedding.batch_size
    stats = {"scanned": 0, "embedded": 0, "failed": 0}

    with get_session() as s:
        rows = (
            s.execute(
                select(RawArticle)
                .where(RawArticle.status.in_(_FILLABLE_STATUSES))
                .where(RawArticle.embedding.is_(None))
                .order_by(RawArticle.id.asc())
            )
            .scalars()
            .all()
        )
        stats["scanned"] = len(rows)
        if not rows:
            print("  [embed] 无需回填，所有目标文章 embedding 已就位")
            return stats

        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            texts = [f"{a.title}\n{a.content}" for a in batch]
            try:
                vecs = embed_texts(texts)
            except Exception as e:
                # 整批失败时降级到逐条，避免一条坏数据带挂全批
                print(
                    f"  [embed] batch encode 失败 → 降级到逐条：{type(e).__name__}: {e}"
                )
                for a, t in zip(batch, texts, strict=True):
                    try:
                        a.embedding = embed_texts([t])[0]
                        stats["embedded"] += 1
                    except Exception as ee:
                        a.last_error = f"embed_failed: {type(ee).__name__}: {ee}"
                        stats["failed"] += 1
                continue

            for a, v in zip(batch, vecs, strict=True):
                a.embedding = v
                stats["embedded"] += 1

    print(
        f"  [embed] scanned={stats['scanned']} embedded={stats['embedded']} "
        f"failed={stats['failed']}"
    )
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 1.3] embed 历史回填")
    print("=" * 60)
    print(run())
