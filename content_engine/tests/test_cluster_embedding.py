"""阶段 2.3 单测：cluster 阶段的 embedding 质心检索 + 增量更新。

只测纯函数（_cos / _normalize / _update_centroid / _maybe_review），
DB 路径在 test_api_smoke / 全链路验证里覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from content_engine.models import EventStatus
from content_engine.stages.cluster import (
    _cos,
    _maybe_review,
    _normalize,
    _update_centroid,
)


def test_cos_basic():
    assert _cos([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)
    assert _cos([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == pytest.approx(0.0)
    assert _cos([1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]) == pytest.approx(-1.0)
    # 边界
    assert _cos([], [1.0]) == 0.0
    assert _cos([0.0, 0.0], [0.0, 0.0]) == 0.0  # 全零返回 0


def test_normalize_unit_length():
    n = _normalize([3.0, 4.0])
    norm = sum(x * x for x in n) ** 0.5
    assert norm == pytest.approx(1.0)
    # 全零向量原样返回，不抛异常
    assert _normalize([0.0, 0.0]) == [0.0, 0.0]


def test_update_centroid_increments_correctly():
    """质心 = (old * n + new) / (n+1) 后再单位化。"""
    old = [1.0, 0.0]
    new = [0.0, 1.0]
    merged = _update_centroid(old, n=1, new_vec=new)
    # 数学上：(1+0)/2, (0+1)/2 = (0.5, 0.5)；归一化后 = (1/√2, 1/√2)
    inv_sqrt2 = 1.0 / (2 ** 0.5)
    assert merged[0] == pytest.approx(inv_sqrt2, abs=1e-6)
    assert merged[1] == pytest.approx(inv_sqrt2, abs=1e-6)


def _fake_member(conf: float | None):
    return SimpleNamespace(cls_confidence=conf)


def test_maybe_review_low_confidence_majority_triggers(monkeypatch):
    """超过 50% 成员低置信 → reviewing。"""
    from content_engine.stages import cluster as cluster_module

    monkeypatch.setattr(cluster_module.settings.threshold, "cls_llm_threshold", 0.6)
    members = [_fake_member(0.3), _fake_member(0.4), _fake_member(0.9)]  # 2/3 低置信
    ev = SimpleNamespace()
    assert _maybe_review(ev, members) == EventStatus.reviewing


def test_maybe_review_high_confidence_keeps_clustered(monkeypatch):
    from content_engine.stages import cluster as cluster_module

    monkeypatch.setattr(cluster_module.settings.threshold, "cls_llm_threshold", 0.6)
    members = [_fake_member(0.9), _fake_member(0.95)]
    ev = SimpleNamespace()
    assert _maybe_review(ev, members) == EventStatus.clustered


def test_maybe_review_empty_members():
    """空成员集合 → clustered（不算异常）。"""
    assert _maybe_review(SimpleNamespace(), []) == EventStatus.clustered
