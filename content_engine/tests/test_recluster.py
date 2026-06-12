"""阶段 2.4 单测：HDBSCAN 离线复核报告生成。

不连真实 DB / 真实 sklearn：
- 直接 mock ``_load_dataset`` 与 ``_run_hdbscan`` 即可覆盖打标分支；
- 三种场景：单事件被拆 / 跨事件被并 / 无变化。
"""

from __future__ import annotations

from types import SimpleNamespace

from content_engine.models import EventStatus, Module
from content_engine.stages import recluster as recluster_module


def _fake_event(ev_id: int):
    return SimpleNamespace(
        id=ev_id,
        module=Module.tech,
        status=EventStatus.summarized,
        needs_split=None,
        suggested_merge_id=None,
    )


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_recluster_marks_split(monkeypatch):
    """事件 1 的 3 个成员被 HDBSCAN 拆到簇 0/1/0 → 应标 needs_split=True。"""
    ev1 = _fake_event(1)
    members = {1: [(101, [1.0, 0.0]), (102, [0.0, 1.0]), (103, [1.0, 0.0])]}

    def _fake_load(session, cutoff):
        return [ev1], members

    def _fake_hdb(vectors, min_cluster_size=2):
        # 与 flat 顺序对齐：第二个被拆到另一个簇
        return [0, 1, 0]

    monkeypatch.setattr(recluster_module, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(recluster_module, "_load_dataset", _fake_load)
    monkeypatch.setattr(recluster_module, "_run_hdbscan", _fake_hdb)

    stats = recluster_module.run(window_hours=24, dry_run=False)
    assert stats["events_marked_split"] == 1
    assert ev1.needs_split is True


def test_recluster_marks_merge(monkeypatch):
    """事件 1 与 事件 2 的成员被分到同一簇 → 较大 id 标 suggested_merge_id=1。"""
    ev1 = _fake_event(1)
    ev2 = _fake_event(2)
    members = {1: [(101, [1.0, 0.0])], 2: [(201, [1.0, 0.0])]}

    def _fake_load(session, cutoff):
        return [ev1, ev2], members

    def _fake_hdb(vectors, min_cluster_size=2):
        return [0, 0]

    monkeypatch.setattr(recluster_module, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(recluster_module, "_load_dataset", _fake_load)
    monkeypatch.setattr(recluster_module, "_run_hdbscan", _fake_hdb)

    stats = recluster_module.run(window_hours=24, dry_run=False)
    assert stats["events_marked_merge"] == 1
    assert ev2.suggested_merge_id == 1
    assert ev1.suggested_merge_id is None  # 较小 id 不被标


def test_recluster_no_change(monkeypatch):
    """每个事件单成员且簇标签均不同 → 既不拆也不并。"""
    ev1 = _fake_event(1)
    ev2 = _fake_event(2)
    members = {1: [(101, [1.0, 0.0])], 2: [(201, [0.0, 1.0])]}

    def _fake_load(session, cutoff):
        return [ev1, ev2], members

    def _fake_hdb(vectors, min_cluster_size=2):
        return [0, 1]

    monkeypatch.setattr(recluster_module, "get_session", lambda: _FakeSession())
    monkeypatch.setattr(recluster_module, "_load_dataset", _fake_load)
    monkeypatch.setattr(recluster_module, "_run_hdbscan", _fake_hdb)

    stats = recluster_module.run(window_hours=24, dry_run=False)
    assert stats["events_marked_split"] == 0
    assert stats["events_marked_merge"] == 0
    assert ev1.needs_split is None
    assert ev2.suggested_merge_id is None
