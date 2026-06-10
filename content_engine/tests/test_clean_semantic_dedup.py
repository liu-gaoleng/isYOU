"""阶段 1.3 单测：clean 阶段的语义去重核心逻辑。

不依赖真实 PG / pgvector：直接构造 stub session 让 ``select(RawArticle)`` 返回伪造记录，
分别覆盖：
1. cos ≥ 0.92 → 命中已 cleaned 文章，返回 (article, sim)；
2. cos < 0.92 → 不命中，返回 (None, sim)；
3. 单位向量 cos 计算正确（_cosine 纯函数）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from content_engine.stages import clean as clean_module
from content_engine.stages.clean import _cosine, _find_semantic_duplicate


def test_cosine_unit_vectors():
    # 完全同向 → 1.0
    assert _cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)
    # 正交 → 0.0
    assert _cosine([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == pytest.approx(0.0)
    # 反向 → -1.0
    assert _cosine([1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]) == pytest.approx(-1.0)
    # 边界：空向量、长度不一致都返回 0
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([1.0, 2.0], [1.0]) == 0.0


def _stub_session(neighbours):
    """构造一个最小的 session：execute(...).scalars().all() 返回 neighbours。"""
    scalars = SimpleNamespace(all=lambda: neighbours)
    result = SimpleNamespace(scalars=lambda: scalars)
    return SimpleNamespace(execute=lambda stmt: result)


def _fake_article(article_id: int, vec: list[float]):
    """伪造一条 RawArticle 行（仅暴露 _find_semantic_duplicate 用到的属性）。"""
    return SimpleNamespace(id=article_id, embedding=vec)


def test_semantic_dup_hits_above_threshold(monkeypatch):
    """同窗内有近邻 cos≈0.99（≥0.92）→ 应当返回该近邻。"""
    # 阈值默认就是 0.92；构造一条接近正前向的近邻向量
    near = _fake_article(101, [0.99, 0.14107, 0.0, 0.0])  # |v|≈1, dot(target,v)=0.99
    far = _fake_article(102, [0.0, 1.0, 0.0, 0.0])  # cos=0
    session = _stub_session([near, far])
    candidate = _fake_article(999, [1.0, 0.0, 0.0, 0.0])
    cutoff = datetime.now(timezone.utc)

    dup, sim = _find_semantic_duplicate(session, candidate, candidate.embedding, cutoff)
    assert dup is near
    assert sim > 0.92


def test_semantic_dup_misses_below_threshold(monkeypatch):
    """同窗内最高 cos=0.5（<0.92）→ 不命中，返回 (None, 0.5)。"""
    a = _fake_article(201, [0.5, 0.8660254, 0.0, 0.0])  # cos with target ≈ 0.5
    b = _fake_article(202, [0.0, 1.0, 0.0, 0.0])
    session = _stub_session([a, b])
    candidate = _fake_article(999, [1.0, 0.0, 0.0, 0.0])
    cutoff = datetime.now(timezone.utc)

    dup, sim = _find_semantic_duplicate(session, candidate, candidate.embedding, cutoff)
    assert dup is None
    assert 0.4 < sim < 0.6


def test_semantic_dup_empty_pool(monkeypatch):
    """同窗无任何已通过文章 → 返回 (None, -1)。"""
    session = _stub_session([])
    candidate = _fake_article(999, [1.0, 0.0, 0.0, 0.0])
    cutoff = datetime.now(timezone.utc)

    dup, sim = _find_semantic_duplicate(session, candidate, candidate.embedding, cutoff)
    assert dup is None
    assert sim == -1.0


def test_semantic_dedup_threshold_uses_settings(monkeypatch):
    """语义去重阈值应从 settings 读取（修改阈值即可改变命中边界）。"""
    # 把阈值临时调到 0.999 → 上面 cos=0.99 的近邻应不命中
    monkeypatch.setattr(
        clean_module.settings.embedding,
        "semantic_dedup_threshold",
        0.999,
    )
    near = _fake_article(101, [0.99, 0.14107, 0.0, 0.0])
    session = _stub_session([near])
    candidate = _fake_article(999, [1.0, 0.0, 0.0, 0.0])
    dup, sim = _find_semantic_duplicate(
        session, candidate, candidate.embedding, datetime.now(timezone.utc)
    )
    assert dup is None
    assert sim == pytest.approx(0.99, abs=1e-3)
