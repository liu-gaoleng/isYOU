"""阶段 2：清洗 + 精确/近似去重 + 语义去重（clean）。

阶段 1.1：质量门槛 + SimHash 64-bit 字面去重（汉明距离 ≤ 阈值）。
阶段 1.3 起追加语义去重：
1. SimHash 通过后，调 ``embed_texts`` 生成 512 维向量（默认 bge-small-zh-v1.5）；
2. 在已 cleaned/classified/clustered 同窗（默认 72h）文章中找余弦最大近邻；
3. 余弦 ≥ 阈值（默认 0.92）即判转载/复述，标 dropped；
4. 否则推进 status=cleaned，``embedding`` 列同时落库供后续阶段使用。

为什么放在 clean 阶段：
- 采集阶段 url + sha256 已挡掉「字节级完全相同」的转载；
- SimHash 能挡住「同文不同 url / 大小写空白差异」；
- Embedding 才能挡住「相同事件不同表述」（媒体改写）；
- 三道闸门完成后再进入分类/聚类/摘要，避免下游做无用功。

embed 失败的兜底：仍标 cleaned 但 embedding 为 NULL，``last_error`` 记原因，不阻塞流水线。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from content_engine.config import settings
from content_engine.models import ArticleStatus, RawArticle, get_session
from content_engine.services.embedding import embed_texts

from .utils import hamming_distance, simhash64

# 阶段 0 的低门槛：太短的"纯标题"条目下放，但留一线给后续清洗
_MIN_CONTENT_LEN = 30
# clean 阶段视为"已通过去重"的状态集合（候选池）
_PASSED_STATUSES = (
    ArticleStatus.cleaned,
    ArticleStatus.classified,
    ArticleStatus.clustered,
)


def _find_near_duplicate(
    session, candidate: RawArticle, candidate_simhash: str, cutoff: datetime
) -> tuple[RawArticle | None, int]:
    """SimHash 字面去重：在时间窗内寻找最近邻 SimHash；返回 (命中的对照文章, 汉明距离)。"""
    threshold = settings.threshold.simhash_hamming_threshold
    rows = (
        session.execute(
            select(RawArticle)
            .where(RawArticle.status.in_(_PASSED_STATUSES))
            .where(RawArticle.simhash.is_not(None))
            .where(RawArticle.fetched_at >= cutoff)
            .where(RawArticle.id != candidate.id)
        )
        .scalars()
        .all()
    )
    best: tuple[RawArticle | None, int] = (None, 65)
    for other in rows:
        d = hamming_distance(candidate_simhash, other.simhash)
        if d < best[1]:
            best = (other, d)
            if d == 0:
                break
    if best[0] is not None and best[1] <= threshold:
        return best
    return None, best[1]


def _cosine(a: list[float], b: list[float]) -> float:
    """两条单位向量的余弦相似度（向量已 L2 归一化时 == 点积）。

    保险起见仍除以模，避免上游忘了归一化导致结果失真。
    """
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


def _find_semantic_duplicate(
    session, candidate: RawArticle, candidate_vec: list[float], cutoff: datetime
) -> tuple[RawArticle | None, float]:
    """语义去重：找 cos 最大的同窗已通过文章；命中阈值则返回。"""
    threshold = settings.embedding.semantic_dedup_threshold
    rows = (
        session.execute(
            select(RawArticle)
            .where(RawArticle.status.in_(_PASSED_STATUSES))
            .where(RawArticle.embedding.is_not(None))
            .where(RawArticle.fetched_at >= cutoff)
            .where(RawArticle.id != candidate.id)
        )
        .scalars()
        .all()
    )
    best: tuple[RawArticle | None, float] = (None, -1.0)
    for other in rows:
        sim = _cosine(candidate_vec, list(other.embedding))
        if sim > best[1]:
            best = (other, sim)
    if best[0] is not None and best[1] >= threshold:
        return best
    return None, best[1]


def run() -> dict:
    stats = {
        "cleaned": 0,
        "dropped_quality": 0,
        "dropped_simhash": 0,
        "dropped_semantic": 0,
        "embed_failed": 0,
    }
    simhash_window = timedelta(hours=settings.threshold.simhash_window_hours)
    semantic_window = timedelta(hours=settings.embedding.semantic_dedup_window_hours)

    with get_session() as s:
        rows = (
            s.execute(
                select(RawArticle).where(RawArticle.status == ArticleStatus.raw)
            )
            .scalars()
            .all()
        )
        for art in rows:
            # 1. 质量门槛
            if not art.title.strip() or len(art.content) < _MIN_CONTENT_LEN:
                art.status = ArticleStatus.dropped
                art.last_error = "quality_gate: empty title or content too short"
                stats["dropped_quality"] += 1
                continue

            # 2. SimHash 字面去重
            sh = simhash64(art.title, art.content)
            if sh is None:
                art.status = ArticleStatus.dropped
                art.last_error = "simhash: empty feature set"
                stats["dropped_quality"] += 1
                continue
            art.simhash = sh

            now = datetime.now(timezone.utc)
            cutoff_simhash = now - simhash_window
            dup, dist = _find_near_duplicate(s, art, sh, cutoff_simhash)
            if dup is not None:
                art.status = ArticleStatus.dropped
                art.last_error = (
                    f"simhash_duplicate: hamming={dist} of article#{dup.id}"
                )
                stats["dropped_simhash"] += 1
                continue

            # 3. 语义去重（embed 失败时降级，仅记录不阻塞）
            try:
                vec = embed_texts([f"{art.title}\n{art.content}"])[0]
                art.embedding = vec
            except Exception as e:  # 任何 provider 失败都不能让流水线整批挂掉
                art.last_error = f"embed_failed: {type(e).__name__}: {e}"
                stats["embed_failed"] += 1
                art.status = ArticleStatus.cleaned
                stats["cleaned"] += 1
                continue

            cutoff_semantic = now - semantic_window
            sem_dup, sim = _find_semantic_duplicate(s, art, vec, cutoff_semantic)
            if sem_dup is not None:
                art.status = ArticleStatus.dropped
                art.last_error = (
                    f"semantic_duplicate: cos={sim:.4f} of article#{sem_dup.id}"
                )
                stats["dropped_semantic"] += 1
                continue

            # 4. 通过
            art.status = ArticleStatus.cleaned
            stats["cleaned"] += 1

    print(
        f"  [clean] cleaned={stats['cleaned']}  "
        f"dropped_quality={stats['dropped_quality']}  "
        f"dropped_simhash={stats['dropped_simhash']}  "
        f"dropped_semantic={stats['dropped_semantic']}  "
        f"embed_failed={stats['embed_failed']}"
    )
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 2/6] clean 清洗 + SimHash + 语义去重")
    print("=" * 60)
    print(run())
