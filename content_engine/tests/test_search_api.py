"""阶段 3.6 单测：/search 搜索端点（校验 / 分页游标 / 模块过滤）。

ILIKE 子串过滤属 SQL 层行为（需真实 PG），此处不复刻；用假会话返回受控行集，
验证端点的参数校验、分页（limit+1 → has_more / next_cursor）、错误处理与
score cursor 编解码往返。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from content_engine.models.enums import EventStatus, Module

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


def _stub_event(event_id: int):
    content = SimpleNamespace(version=1, title=f"AI 芯片{event_id}", sources=[], deep_content=None)
    return SimpleNamespace(
        id=event_id,
        module=Module.tech,
        status=EventStatus.published,
        card_summary=f"卡片{event_id}",
        detail_summary="详情",
        tags=["AI"],
        importance=float(100 - event_id),
        hotness=0.5,
        source_count=1,
        first_seen=NOW,
        last_update=NOW,
        contents=[content],
    )


@pytest.fixture
def client(monkeypatch):
    # 端点用 limit+1 判 has_more；返回 N 行受 _rows 控制
    state = {"rows": []}

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return SimpleNamespace(all=lambda: list(self._rows))

    class _FakeSession:
        def execute(self, _stmt):
            return _Result(state["rows"])

        def get(self, _model, _id):
            return None

    @contextmanager
    def fake_get_session():
        yield _FakeSession()

    from content_engine.api.routers import brief as brief_mod

    monkeypatch.setattr(brief_mod, "get_session", fake_get_session)

    from content_engine.api.app import app

    with TestClient(app) as c:
        c._state = state
        c._brief = brief_mod
        yield c


def test_search_requires_q(client):
    # 缺 q → FastAPI 422
    assert client.get("/api/v1/search").status_code == 422


def test_search_empty_result(client):
    client._state["rows"] = []
    r = client.get("/api/v1/search?q=不存在的词")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["next_cursor"] is None


def test_search_returns_cards(client):
    client._state["rows"] = [_stub_event(1), _stub_event(2)]
    r = client.get("/api/v1/search?q=AI&limit=20")
    assert r.status_code == 200
    body = r.json()
    assert [i["id"] for i in body["items"]] == [1, 2]
    assert body["next_cursor"] is None  # 未超 limit


def test_search_pagination_sets_cursor(client):
    # limit=2，返回 3 行（limit+1）→ has_more=True，给 next_cursor
    client._state["rows"] = [_stub_event(1), _stub_event(2), _stub_event(3)]
    r = client.get("/api/v1/search?q=AI&limit=2")
    body = r.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None
    # 下一页带上 cursor 不应报错
    r2 = client.get(f"/api/v1/search?q=AI&limit=2&cursor={body['next_cursor']}")
    assert r2.status_code == 200


def test_search_invalid_module_400(client):
    client._state["rows"] = []
    r = client.get("/api/v1/search?q=AI&module=sports")
    assert r.status_code == 400


def test_search_invalid_cursor_400(client):
    client._state["rows"] = []
    r = client.get("/api/v1/search?q=AI&cursor=not-base64!!")
    assert r.status_code == 400


def test_score_cursor_roundtrip(client):
    enc = client._brief._encode_score_cursor(12.5, 99)
    assert client._brief._decode_score_cursor(enc) == (12.5, 99)
