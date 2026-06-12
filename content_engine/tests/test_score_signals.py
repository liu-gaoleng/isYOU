"""阶段 3.3 单测：compute_hotness 真实热度信号（纯函数，不连 DB）。

hotness = 0.5·cross_source + 0.3·social_ratio + 0.2·freshness_velocity，返回 [0,1]。
用轻量假对象模拟 EventArticle 链接结构：link.article.{source_id, source.level, fetched_at}。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from content_engine.models import SourceLevel
from content_engine.stages.score import compute_hotness

NOW = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class _Source:
    level: SourceLevel


@dataclass
class _Article:
    source_id: int
    source: _Source
    fetched_at: datetime | None


@dataclass
class _Link:
    article: _Article


def _link(source_id: int, level: SourceLevel, age_hours: float = 0.0) -> _Link:
    fetched = NOW - timedelta(hours=age_hours)
    return _Link(_Article(source_id, _Source(level), fetched))


def test_empty_members_zero():
    assert compute_hotness([], now=NOW) == 0.0


def test_single_a_source_fresh():
    # 1 源 → cross=0.2；A 级 → social_ratio=0；24h 内 → freshness=1.0
    members = [_link(1, SourceLevel.A, age_hours=1)]
    expected = round(0.5 * 0.2 + 0.3 * 0.0 + 0.2 * 1.0, 4)
    assert compute_hotness(members, now=NOW) == expected


def test_five_distinct_sources_saturate_cross():
    # 5 个不同源 → cross=min(5/5,1)=1.0；全 B 级 → social=1.0；全新鲜 → 1.0
    members = [_link(i, SourceLevel.B, age_hours=2) for i in range(1, 6)]
    assert compute_hotness(members, now=NOW) == 1.0


def test_social_ratio_partial():
    # 2 源：1 个 B（社交）+ 1 个 S → social_ratio=0.5
    members = [_link(1, SourceLevel.B, age_hours=1), _link(2, SourceLevel.S, age_hours=1)]
    # cross=2/5=0.4, social=0.5, freshness=1.0
    expected = round(0.5 * 0.4 + 0.3 * 0.5 + 0.2 * 1.0, 4)
    assert compute_hotness(members, now=NOW) == expected


def test_stale_members_lower_freshness():
    # 全部超过 24h → freshness_velocity=0
    members = [_link(1, SourceLevel.A, age_hours=100), _link(2, SourceLevel.A, age_hours=200)]
    # cross=2/5=0.4, social=0, freshness=0
    expected = round(0.5 * 0.4, 4)
    assert compute_hotness(members, now=NOW) == expected


def test_fetched_at_none_counts_as_stale():
    link = _Link(_Article(1, _Source(SourceLevel.B), None))
    # 1 源 cross=0.2, social=1.0(B), freshness=0(None)
    expected = round(0.5 * 0.2 + 0.3 * 1.0, 4)
    assert compute_hotness([link], now=NOW) == expected


def test_result_in_unit_range():
    members = [_link(i, SourceLevel.B, age_hours=1) for i in range(1, 10)]
    h = compute_hotness(members, now=NOW)
    assert 0.0 <= h <= 1.0
