"""阶段 3.5 单测：/ranking 热榜端点（全站 + 分模块 + Redis 命中 / DB 回退）。

不依赖真实 PG/Redis：
- monkeypatch brief.ranking.top 控制「Redis 命中」与「降级返回 None」两条路径；
- monkeypatch brief.get_session 用假会话按 id 返回 stub 事件，验证端点按 ZSet 顺序输出。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from content_engine.models.enums import EventStatus, Module

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _stub_event(event_id: int, module=Module.tech, status=EventStatus.published):
    content = SimpleNamespace(version=1, title=f"事件{event_id}", sources=[], deep_content=None)
    return SimpleNamespace(
        id=event_id,
        module=module,
        status=status,
        card_summary=f"卡片{event_id}",
        detail_summary="详情",
        tags=["标签"],
        importance=float(event_id),
        hotness=0.5,
        source_count=1,
        first_seen=NOW,
        last_update=NOW,
        contents=[content],
    )


@pytest.fixture
def client(monkeypatch):
    # 可见事件池：测试可控
    events: dict[int, object] = {
        1: _stub_event(1, Module.tech),
        2: _stub_event(2, Module.tech),
        3: _stub_event(3, Module.finance),
    }

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _FakeSession:
        """支持 ranking 端点两条路径：
        - Redis 命中：执行 select(Event).where(id.in_(ids)) → 返回池中对应事件；
        - DB 回退：执行 order_by importance → 返回全部可见事件。
        简化处理：统一返回池中全部，端点自身按 ids 顺序重排（命中路径），
        回退路径直接用返回顺序。
        """

        def __init__(self):
            self.last_ids = None

        def execute(self, stmt):
            # 命中路径会带 id.in_(ids)，回退路径不带；这里都返回全部可见事件，
            # 端点命中分支会用 by_id 重排，回退分支用此顺序。
            visible = [e for e in events.values() if e.status in (
                EventStatus.summarized, EventStatus.scored, EventStatus.published)]
            # 回退分支期望按 importance 倒序
            visible_sorted = sorted(visible, key=lambda e: e.importance, reverse=True)
            return _Result(visible_sorted)

        def get(self, _model, event_id):
            return events.get(event_id)

    @contextmanager
    def fake_get_session():
        yield _FakeSession()

    from content_engine.api.routers import brief as brief_mod

    monkeypatch.setattr(brief_mod, "get_session", fake_get_session)

    from content_engine.api.app import app

    with TestClient(app) as c:
        c._events = events
        c._brief = brief_mod
        c._mp = monkeypatch
        yield c


def test_ranking_redis_hit_orders_by_zset(client):
    # Redis 命中：返回 [3,1,2]，端点应按该顺序输出（而非 importance 倒序）
    client._mp.setattr(client._brief.ranking, "top", lambda m, n: [3, 1, 2])
    r = client.get("/api/v1/ranking?limit=10")
    assert r.status_code == 200, r.text
    ids = [item["id"] for item in r.json()]
    assert ids == [3, 1, 2]


def test_ranking_redis_hit_skips_invisible(client):
    # 3 变不可见，命中 ids 含 3 应被跳过
    client._events[3] = _stub_event(3, Module.finance, status=EventStatus.clustered)
    client._mp.setattr(client._brief.ranking, "top", lambda m, n: [3, 1, 2])
    ids = [item["id"] for item in client.get("/api/v1/ranking").json()]
    assert ids == [1, 2]


def test_ranking_db_fallback_when_redis_down(client):
    # Redis 降级返回 None → 走 DB importance 倒序
    client._mp.setattr(client._brief.ranking, "top", lambda m, n: None)
    r = client.get("/api/v1/ranking?limit=10")
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()]
    assert ids == [3, 2, 1]  # importance 倒序


def test_ranking_module_filter_passes_module(client):
    captured = {}

    def fake_top(module, n):
        captured["module"] = module
        return [1, 2]

    client._mp.setattr(client._brief.ranking, "top", fake_top)
    r = client.get("/api/v1/ranking?module=tech&limit=5")
    assert r.status_code == 200
    assert captured["module"] == "tech"


def test_ranking_invalid_module_400(client):
    client._mp.setattr(client._brief.ranking, "top", lambda m, n: [1])
    r = client.get("/api/v1/ranking?module=sports")
    assert r.status_code == 400
