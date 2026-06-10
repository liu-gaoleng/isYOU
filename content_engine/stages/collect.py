"""阶段 1：采集（collect）+ 信源健康监控（阶段 1.2）。

读 sources 表中 enabled=True 的信源，逐源拉 RSS，
将新文章写入 raw_articles（status=raw），url 重复则跳过（依赖 UniqueConstraint）。

每次拉取（无论成功/失败）写一条 source_health 记录：
- success / failed / partial 三态；
- 累计 consecutive_failures，达阈值（settings.threshold.source_failure_alert_threshold）
  通过 logging.WARNING 告警（控制台日志为主，CMS 后台后续读 source_health 表展示）。

阶段 1.3 会扩展 API/爬虫子模块。
"""

from __future__ import annotations

import logging
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from content_engine.config import settings
from content_engine.models import RawArticle, Source, SourceHealth, get_session

from .utils import clean_text, content_hash

try:
    import feedparser  # type: ignore
except ImportError as e:  # pragma: no cover
    raise SystemExit("缺少依赖 feedparser，请先安装：pip install -e '.[dev]'") from e

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl._create_unverified_context()

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

_logger = logging.getLogger("content_engine.collect")


def _parse_feed(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
        raw = resp.read()
    return feedparser.parse(raw)


def _parse_published(entry) -> datetime | None:
    s = entry.get("published") or entry.get("updated") or ""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _last_consecutive_failures(session, source_id: int) -> int:
    """读取该信源最近一次 health 记录的 consecutive_failures（不存在返回 0）。"""
    last = session.execute(
        select(SourceHealth)
        .where(SourceHealth.source_id == source_id)
        .order_by(desc(SourceHealth.fetched_at))
        .limit(1)
    ).scalar_one_or_none()
    return last.consecutive_failures if last else 0


def _record_health(
    session,
    source: Source,
    *,
    fetched_at: datetime,
    status: str,
    item_count: int,
    inserted_count: int,
    latency_ms: int | None,
    error: str | None,
) -> SourceHealth:
    """写入一条健康记录，并维护 consecutive_failures 计数。

    成功（success / partial）→ 归零；失败（failed）→ 累加。
    """
    prev = _last_consecutive_failures(session, source.id)
    if status == "failed":
        consecutive = prev + 1
    else:
        consecutive = 0
    rec = SourceHealth(
        source_id=source.id,
        fetched_at=fetched_at,
        status=status,
        item_count=item_count,
        inserted_count=inserted_count,
        latency_ms=latency_ms,
        error=(error or None),
        consecutive_failures=consecutive,
    )
    session.add(rec)
    return rec


def _collect_one(source: Source) -> tuple[int, int, int]:
    """从单个信源拉取，返回 (新增, 已存在/跳过, 解析得到的总条目数)。"""
    feed = _parse_feed(source.url)
    inserted = 0
    skipped = 0
    total = len(feed.entries)
    now = datetime.now(timezone.utc)
    with get_session() as s:
        for entry in feed.entries[: settings.threshold.max_per_source]:
            title = clean_text(entry.get("title", ""))
            content = clean_text(entry.get("summary", entry.get("description", "")))
            url = (entry.get("link") or "").strip()
            if not title or not url:
                skipped += 1
                continue
            article = RawArticle(
                source_id=source.id,
                url=url[:1024],
                title=title[:512],
                content=content,
                raw_hash=content_hash(title, content),
                published_at=_parse_published(entry),
                fetched_at=now,
            )
            s.add(article)
            try:
                s.flush()
                inserted += 1
            except IntegrityError:
                # 触发 uq_raw_articles_url：已采集过，跳过
                s.rollback()
                skipped += 1
    return inserted, skipped, total


def run() -> dict:
    """阶段入口：遍历启用信源采集 + 写健康记录。"""
    stats = {"sources": 0, "inserted": 0, "skipped": 0, "failed": 0, "alerted": 0, "errors": []}
    alert_threshold = settings.threshold.source_failure_alert_threshold

    with get_session() as s:
        sources = s.execute(select(Source).where(Source.enabled.is_(True))).scalars().all()
    stats["sources"] = len(sources)

    for src in sources:
        t0 = time.monotonic()
        fetched_at = datetime.now(timezone.utc)
        try:
            ins, skip, total = _collect_one(src)
            latency_ms = int((time.monotonic() - t0) * 1000)
            # 解析成功 + 0 新增也算 success，避免热点冷却期误报
            status = "success" if total > 0 else "partial"
            stats["inserted"] += ins
            stats["skipped"] += skip
            with get_session() as s:
                _record_health(
                    s, src,
                    fetched_at=fetched_at, status=status,
                    item_count=total, inserted_count=ins,
                    latency_ms=latency_ms, error=None,
                )
            print(f"  [collect] {src.name:14s} 新增 {ins:>3d}  跳过 {skip:>3d}  总 {total:>3d}  {latency_ms}ms")
        except Exception as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            err_msg = f"{type(e).__name__}: {e}"[:1024]
            stats["failed"] += 1
            stats["errors"].append({"source": src.name, "error": err_msg})
            with get_session() as s:
                rec = _record_health(
                    s, src,
                    fetched_at=fetched_at, status="failed",
                    item_count=0, inserted_count=0,
                    latency_ms=latency_ms, error=err_msg,
                )
                # rec 在事务 commit 前先取计数
                consecutive = rec.consecutive_failures
            print(f"  [collect] {src.name:14s} 失败：{err_msg}  连续失败={consecutive}")
            if consecutive >= alert_threshold:
                stats["alerted"] += 1
                _logger.warning(
                    "信源 [%s] 连续失败 %d 次 (≥%d)，请检查 url=%s 错误=%s",
                    src.name, consecutive, alert_threshold, src.url, err_msg,
                )
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("=" * 60)
    print("[阶段 1/6] collect 采集 + 健康监控")
    print("=" * 60)
    print(run())
